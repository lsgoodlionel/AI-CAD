"""轴网识别 Celery 任务(Phase I 接入系统)。

**扇出模式**:与 `drawing_info_extract` 同构——项目级任务只查清单并逐图派发,
真正的识别由单图任务独立执行(可重试、互不拖累)。单图识别含 OCR,
实测每张 A0 图数秒到数十秒,串行必撞 celery 硬超时。

**幂等**:识别结果一图一行覆盖;人工确认过的分区编号在**另一张表**,
重跑会把它带回来,不会被冲掉。
"""
from __future__ import annotations

import asyncio
import logging

import databases

from core.celery_app import celery_app
from core.config import settings

logger = logging.getLogger(__name__)

#: 只对可能含轴网的图纸跑识别。PDF 之外的格式先转不了矢量,直接跳过
_SELECT_PROJECT_DRAWINGS = """
SELECT id FROM drawings
WHERE project_id = CAST(:project_id AS uuid)
  AND file_key ILIKE '%.pdf'
ORDER BY created_at
"""

_SELECT_ONE = """
SELECT id, project_id, file_key, drawing_no, title FROM drawings
WHERE id = CAST(:drawing_id AS uuid)
"""


def _task_db() -> databases.Database:
    """任务自己的连接(celery worker 无 app 共享连接)。"""
    return databases.Database(settings.database_url)


@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def recognize_project_axes(self, project_id: str) -> dict:
    """全项目轴网识别入口(扇出)。"""
    try:
        return asyncio.run(_fanout(project_id))
    except Exception as exc:  # noqa: BLE001
        logger.error("[axis_recognition] 项目扇出失败 %s: %s", project_id, exc)
        raise self.retry(exc=exc)


async def _fanout(project_id: str) -> dict:
    db = _task_db()
    await db.connect()
    try:
        rows = await db.fetch_all(_SELECT_PROJECT_DRAWINGS,
                                  {"project_id": project_id})
    finally:
        await db.disconnect()
    for row in rows:
        recognize_drawing_axes.delay(str(row["id"]))
    result = {"project_id": project_id, "enqueued": len(rows)}
    logger.info("[axis_recognition] 扇出完成: %s", result)
    return result


@celery_app.task(bind=True, max_retries=1, default_retry_delay=30)
def recognize_drawing_axes(self, drawing_id: str) -> dict:
    """单图轴网识别 → 落库 + 写锚点 + 补坐标变换。"""
    try:
        return asyncio.run(_recognize_one(drawing_id))
    except Exception as exc:  # noqa: BLE001
        logger.error("[axis_recognition] 单图失败 %s: %s", drawing_id, exc)
        try:
            asyncio.run(_mark_failed(drawing_id, str(exc)))
        except Exception as mark_exc:  # noqa: BLE001
            # 记失败本身失败了不能盖掉原始错误 —— 否则排查时看到的是
            # 「RuntimeError: asyncio.run() cannot be called from a running
            # event loop」这类二次错误,真正的原因反而没了
            logger.warning("[axis_recognition] 失败状态未能落库 %s: %s",
                           drawing_id, mark_exc)
        raise self.retry(exc=exc)


async def _mark_failed(drawing_id: str, message: str) -> None:
    from services.axis_recognition_repo import save_result

    db = _task_db()
    await db.connect()
    try:
        row = await db.fetch_one(_SELECT_ONE, {"drawing_id": drawing_id})
        if row is None:
            return
        await save_result(db, project_id=str(row["project_id"]),
                          drawing_id=drawing_id, result={}, status="failed",
                          error=message[:500])
    except Exception:  # noqa: BLE001 — 记失败本身失败了不该再抛
        logger.debug("[axis_recognition] 失败状态未能落库 %s", drawing_id)
    finally:
        await db.disconnect()


def _empty_result(row) -> dict:
    """非几何图的空结果 —— 带上原因，不做静默空返回。"""
    from services.axis_recognition import NON_GEOMETRIC_WARNING

    return {"page_w": 0.0, "page_h": 0.0, "circle_count": 0,
            "additional_count": 0, "axis_count": 0, "leader_count": 0,
            "zones": [], "axes": [], "anchors": [], "outliers": [],
            "violations": [], "transform": None, "is_split_view": False,
            "split_view_numbering": None, "suspect_symbol_field": False,
            "warnings": [NON_GEOMETRIC_WARNING]}


async def _recognize_one(drawing_id: str) -> dict:
    from core.model3d.axis_label_circle import circles_from_pdf
    from core.model3d.axis_label_glyph import strokes_from_pdf
    from core.model3d.vector_axis_extractor import segments_from_pdf
    from core.storage import get_file_bytes
    from services.axis_recognition import (
        NON_GEOMETRIC_WARNING, recognize, should_skip_axes, summarize,
    )
    from services.axis_recognition_repo import (
        fetch_zone_labels, save_result,
    )
    from services.axis_world_anchors import persist_anchors, transform_from_axes
    from services.drawing_transform import (
        TRANSFORM_SOURCE_AXES, clear_transform, persist_transform,
    )

    db = _task_db()
    await db.connect()
    try:
        row = await db.fetch_one(_SELECT_ONE, {"drawing_id": drawing_id})
        if row is None:
            return {"drawing_id": drawing_id, "skipped": "not_found"}
        project_id = str(row["project_id"])
        # **非几何图不产出轴网**（系统图/原理图/接线图不表达平面位置）。
        # 判据早就在 `drawing_role` 里，识别层此前没读它 —— 实测
        # 「消火栓系统原理图」被识别出 385 条轴线、21 个分区。
        # **置零而不跳过**：界面上才能与「还没跑」分开（降级必须可见）。
        if should_skip_axes(dict(row)):
            empty = _empty_result(row)
            await save_result(db, project_id=project_id, drawing_id=drawing_id,
                              result=empty)
            logger.info("[axis_recognition] 非几何图不产出轴网: %s",
                        row["drawing_no"] or drawing_id)
            return {"drawing_id": drawing_id, "axis_count": 0,
                    "skipped": "non_geometric"}
        pdf = get_file_bytes(row["file_key"])

        circles = circles_from_pdf(pdf)
        segments, page_w, page_h = segments_from_pdf(pdf)
        zone_labels = await fetch_zone_labels(db, drawing_id)

        # 已落库的比例（几何路径读的）。**一次查询两处用**：
        # ① 供「轴距过密」判据算米轴距（识别自身的 RANSAC 比例多数图没有）；
        # ② 供 `_transform_of` 在自身无比例时借用。
        existing = await db.fetch_one(
            "SELECT scale_m_pt FROM drawing_transform WHERE drawing_id = :d",
            {"d": drawing_id})
        stored_scale = float(existing["scale_m_pt"]) if existing else None
        result = recognize(
            circles["circles"], strokes=strokes_from_pdf(pdf),
            segments=segments, page_w=page_w, page_h=page_h,
            read_text=_ocr_reader(pdf), zone_labels=zone_labels,
            scale_m_pt=stored_scale,
            # 原始候选用于判「缺口处有没有圈」——真漏检 vs 不等跨
            circle_candidates=circles.get("candidates"))

        await save_result(db, project_id=project_id, drawing_id=drawing_id,
                          result=result)
        if result["anchors"]:
            await persist_anchors(db, project_id, drawing_id, result["anchors"])
        # 借用已落库的比例——本路径常有原点没比例，见 _transform_of
        transform = _transform_of(result, transform_from_axes,
                                  fallback_scale=stored_scale)
        if transform:
            await persist_transform(db, project_id=project_id,
                                    drawing_id=drawing_id, transform=transform)
        else:
            # **算不出就要让旧值失效**：实测 S-0-20-102.04C 识别跑于 06:02，
            # 而它的变换停在 01:47（origin_x=0），下游一直在用那条过时的值。
            # 只清 `axes` 来源 —— 几何路径的产出与人工确认值不受影响。
            await clear_transform(db, drawing_id=drawing_id,
                                  source=TRANSFORM_SOURCE_AXES)
        summary = summarize(result)
        logger.info("[axis_recognition] %s → %s", drawing_id, summary)
        return {"drawing_id": drawing_id, **summary}
    finally:
        await db.disconnect()


def _transform_of(result: dict, builder, fallback_scale: float | None = None):
    """识别结果 → DrawingTransform;比例未定则不落(下游诚实降级)。

    `fallback_scale` 是**已落库的比例**（几何路径从图面文字读的）。
    两条路径常常各握一半：轴网路径靠坐标标注做 RANSAC，没有标注就拿不到
    比例；而几何路径能读到比例、原点却靠启发式，实测 149 张缺一个方向。

    有原点没比例时借用它，好过整条放弃 —— 轴网原点依据更强
    （Phase I 轴号圈在真值图上 100% 精确，几何路径的 `_detect_axes` 是启发式）。
    借来的比例仍要过 §6.0.4 门禁：历史行可能写于门禁之前（1:335 万那批）。
    """
    from services.drawing_transform import is_scale_plausible

    transform = result.get("transform") or {}
    scale = transform.get("scale_m_pt")
    if (not scale or scale <= 0) and fallback_scale and fallback_scale > 0:
        scale = fallback_scale
    if not scale or scale <= 0:
        return None
    # **门禁对自身比例同样生效**（回归修复）：上一版只拦借来的比例，
    # 于是重跑后 22 条 `axes` 变换超出国标区间，最离谱 1:654464，
    # 且 confidence 恒为 1.0 —— 与 `transform_from_geometry` 的
    # 1:335 万一模一样：错值带着满分置信度骗过所有下游。
    if not is_scale_plausible(scale):
        return None
    # **不吸附到 §6.0.4 标准值**：吸附是为修正「读文字 + 几何估算」的测量
    # 误差，而这里的比例来自坐标标注 RANSAC 拟合，有残差可验证
    # （Phase I 实测 0.142757 m/pt、残差 5.7 毫米，对应分母 404.7）。
    # 吸附到 400 会引入 0.9% 误差 —— 在 100 米建筑上就是 0.9 米，
    # 把 Phase I 的 6.1 毫米精度毁掉。**有验证的实测值优于规范表。**
    return builder(result["axes"], page_h=result["page_h"], scale_m_pt=scale)


def _ocr_reader(pdf_bytes: bytes):
    """构造 read_text(leader):裁坐标文字的小图做 OCR。

    坐标文字比轴号大一个量级,同一套 RapidOCR 在这里实测置信 0.96~1.00。
    **窗口按引线尺度算**(`text_crop_rect`)——固定窗口在引线短的图上
    会一次框进多处标注,实测 A-01-04A 因此 20 条引线只剩 6 条内点。
    后端不可用时返回空,识别的轴线部分照常出结果。
    """
    dpi = 600

    def read(leader):
        try:
            import fitz
            import numpy as np

            from core.model3d.ocr.service import _select_backend

            backend = _select_backend([])
            if backend is None or not backend.is_available():
                return []
            from core.model3d.coord_annotation import text_crop_rect

            page = fitz.open(stream=pdf_bytes, filetype="pdf")[0]
            zoom = dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                  clip=fitz.Rect(*text_crop_rect(leader)))
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n)[:, :, :3]
            return [t for t, _bbox, _conf in backend.recognize(img, [])]
        except Exception as exc:  # noqa: BLE001 — OCR 失败只降级坐标部分
            logger.debug("[axis_recognition] OCR 裁图失败: %s", exc)
            return []

    return read
