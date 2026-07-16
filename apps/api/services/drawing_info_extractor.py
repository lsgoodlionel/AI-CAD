"""工程信息抽取编排(Phase E1-1)。

把既有抽取器的产物统一收敛为 ``drawing_extracted_info`` 行(每条强制携带
drawing 溯源),分两层:

- ``build_info_items``:纯函数,合成几何/OCR/文件名 → 信息条目 list[dict],
  离线可测,不做任何 IO;
- ``extract_drawing_info``:从文件字节出发构建 geom+ocr 再委托纯函数
  (供 Celery 任务/构建管线调用);
- ``persist_drawing_info``:覆盖式落库(先删后插,幂等),对齐
  services/model_topology.py 的仓储风格。

通用性纪律(蓝图第 8 项):不含任何项目专有词表;分类完全复用
``core/model3d/ocr/classify.classify_text`` 的确定性规则。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.model3d.ocr.classify import classify_text
from core.model3d.ocr.types import OcrResult
from core.model3d.types import DrawingGeometry
from services.drawing_filename_parser import parse_drawing_filename

logger = logging.getLogger(__name__)

# OCR token 入库置信门槛(与 consume._DEFAULT_MIN_CONF 对齐:低置信读错比缺失糟)
OCR_MIN_CONFIDENCE = 0.6
# 单图条目上限(防说明书级图纸刷爆表;超限截断并记 warning)
MAX_ITEMS_PER_DRAWING = 2000


# ── 纯函数层 ─────────────────────────────────────────────────────

def _value_json_for(kind: str, value: float | None) -> dict | None:
    if value is None:
        return None
    if kind == "elevation":
        return {"elevation_m": value}
    if kind == "dimension":
        return {"dim_mm": value}
    return None


def _items_from_vector_texts(geom: DrawingGeometry) -> list[dict]:
    items: list[dict] = []
    for x, y, content in geom.texts:
        text = (content or "").strip()
        if not text:
            continue
        kind, value = classify_text(text)
        items.append({
            "category": kind,
            "content": text,
            "value_json": _value_json_for(kind, value),
            "location_json": {"x": float(x), "y": float(y)},
            "extractor": "vector_text",
            "confidence": None,  # 矢量文字为确定性来源
        })
    return items


def _items_from_ocr(ocr: OcrResult) -> list[dict]:
    if not ocr.available:
        return []
    items: list[dict] = []
    for token in ocr.tokens:
        if token.confidence < OCR_MIN_CONFIDENCE:
            continue
        items.append({
            "category": token.kind,
            "content": token.text,
            "value_json": _value_json_for(token.kind, token.value),
            "location_json": {"bbox": list(token.bbox)},
            "extractor": "ocr",
            "confidence": round(token.confidence, 4),
        })
    return items


def _item_from_filename(filename: str) -> dict | None:
    parsed = parse_drawing_filename(filename)
    if not parsed:
        return None
    return {
        "category": "title_block",
        "content": filename,
        "value_json": parsed,
        "location_json": None,
        "extractor": "filename",
        "confidence": None,
    }


def _dedup(items: list[dict]) -> list[dict]:
    """同 (category, content) 只留最高置信;确定性来源(confidence=None)视为 1.0。"""
    def _conf(it: dict) -> float:
        return 1.0 if it["confidence"] is None else float(it["confidence"])

    best: dict[tuple[str, str], dict] = {}
    for it in items:
        key = (it["category"], it["content"])
        if key not in best or _conf(it) > _conf(best[key]):
            best[key] = it
    # 保持原有顺序输出
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for it in items:
        key = (it["category"], it["content"])
        if key in seen:
            continue
        seen.add(key)
        out.append(best[key])
    return out


def build_info_items(
    *,
    geom: DrawingGeometry | None,
    ocr: OcrResult | None,
    filename: str | None,
) -> list[dict]:
    """合成所有来源的抽取条目(纯函数,可离线测)。"""
    items: list[dict] = []
    if geom is not None:
        items.extend(_items_from_vector_texts(geom))
    if ocr is not None:
        items.extend(_items_from_ocr(ocr))
    if filename:
        fn_item = _item_from_filename(filename)
        if fn_item:
            items.append(fn_item)

    items = _dedup(items)
    if len(items) > MAX_ITEMS_PER_DRAWING:
        logger.warning(
            "[drawing_info] 条目超上限截断: %d -> %d", len(items), MAX_ITEMS_PER_DRAWING
        )
        items = items[:MAX_ITEMS_PER_DRAWING]
    return items


# ── IO 编排层 ────────────────────────────────────────────────────

def extract_drawing_info(
    file_bytes: bytes, file_ext: str, *, filename: str | None = None,
    run_ocr_pass: bool = True,
) -> list[dict]:
    """从文件字节抽取信息条目(几何 + 可选 OCR + 文件名)。

    抽取器缺失/失败一律优雅降级为跳过该来源(不抛),与建模管线纪律一致。
    """
    geom: DrawingGeometry | None = None
    ext = (file_ext or "").lower().lstrip(".")
    try:
        if ext == "pdf":
            from core.model3d.geometry_extractor import extract_pdf_geometry
            geom = extract_pdf_geometry(file_bytes)
        elif ext == "dxf":
            from core.model3d.geometry_extractor import extract_dxf_geometry
            geom = extract_dxf_geometry(file_bytes)
    except Exception as exc:  # noqa: BLE001 — 单图失败不拖垮批量
        logger.warning("[drawing_info] 几何抽取失败(%s): %s", ext, exc)

    ocr_result: OcrResult | None = None
    if run_ocr_pass and ext in ("pdf", "png", "jpg", "jpeg"):
        try:
            from core.model3d.ocr import run_ocr
            ocr_result = run_ocr(file_bytes, f".{ext}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[drawing_info] OCR 失败: %s", exc)

    return build_info_items(geom=geom, ocr=ocr_result, filename=filename)


# ── 持久化仓储(重抽只覆盖 auto 行,保留人审 verified) ───────────────
#
# 关键:重抽绝不能冲掉人工 verified 修正(档案层单一真相源正确性地基,
# 见蓝图 §0.5 决策②)。故只删本图 source_kind='auto' 的行,verified 保留;
# 且删除的是 active auto——被 verified 经 supersedes 置为 is_active=false 的
# auto 也一并清掉(它们已被人审推翻,重抽的新 auto 会重新落 active)。

_DELETE_SQL = (
    "DELETE FROM drawing_extracted_info "
    "WHERE drawing_id = :drawing_id AND source_kind = 'auto'"
)

_INSERT_SQL = """
INSERT INTO drawing_extracted_info (
    project_id, drawing_id, category, content,
    value_json, location_json, extractor, confidence, extraction_version
)
VALUES (
    :project_id, :drawing_id, :category, :content,
    CAST(:value_json AS jsonb), CAST(:location_json AS jsonb),
    :extractor, :confidence, :extraction_version
)
"""


async def persist_drawing_info(
    db: Any, *, project_id: str, drawing_id: str, items: list[dict], version: int = 1,
) -> int:
    """覆盖式落库单图抽取结果,返回写入条数。"""
    await db.execute(_DELETE_SQL, {"drawing_id": drawing_id})
    written = 0
    for it in items:
        await db.execute(_INSERT_SQL, {
            "project_id": project_id,
            "drawing_id": drawing_id,
            "category": it["category"],
            "content": it["content"],
            "value_json": json.dumps(it.get("value_json"), ensure_ascii=False)
                if it.get("value_json") is not None else None,
            "location_json": json.dumps(it.get("location_json"), ensure_ascii=False)
                if it.get("location_json") is not None else None,
            "extractor": it["extractor"],
            "confidence": it.get("confidence"),
            "extraction_version": version,
        })
        written += 1
    return written
