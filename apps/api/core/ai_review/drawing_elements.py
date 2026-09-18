"""审图路径上的构件供给：把一张图现场识别成构件，喂给合理性引擎。

**为什么需要它**：视觉引擎只做 OCR 与图元检测，`ctx.ocr_metadata` 里没有
柱/墙/梁/板。合理性引擎要判「这根柱立不立得住」，得先有柱。

**为什么不复用 `services/model_elements`**：那一层带 DB（图纸表、变换表、
档案表），而引擎这一层刻意无 DB 依赖。这里只用三样东西：对象存储取字节、
几何抽取、识别器 —— 与建模主链路同一个 `recognize()`，因此判据一致。

**三道闸**，都是为了不让一个增强能力拖垮审图主流程：
① 只处理 PDF（DWG/DXF 走另一条解析链，未验证过）；
② 单图硬超时（识别一张 5~10 秒，但病态图能到分钟级，建模那边实测过）；
③ 任何异常都吞成「没有构件」**并写日志** —— 引擎侧会因此报「降级」，
   降级在报告里看得见，不会伪装成「查过了没问题」。
"""
from __future__ import annotations

import concurrent.futures
import logging

logger = logging.getLogger(__name__)

#: 单图识别超时（秒）。与建模链路同量级；超时按「没有构件」处理。
RECOGNIZE_TIMEOUT_SEC = 60

#: 文件大小上限（MB）。超大图纸的识别时间与内存都不可控，
#: 而审图是在线请求链路（Celery 任务，但共享 8GB 的机器）。
MAX_FILE_MB = 80


def _recognize(file_key: str, discipline: str, drawing_id: str, title: str) -> dict | None:
    from core.model3d.element_recognizer import recognize
    from core.model3d.geometry_extractor import extract_pdf_geometry
    from core.storage import get_file_bytes

    data = get_file_bytes(file_key)
    if not data:
        return None
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        logger.info("合理性取构件跳过：%s 超过 %s MB", file_key, MAX_FILE_MB)
        return None
    elements = recognize(extract_pdf_geometry(data), discipline, drawing_id,
                         drawing_title=title, view_type="plan")
    return elements.as_dict()


def elements_for_review(ctx) -> dict | None:
    """`DrawingContext` → 构件字典（`FloorElements.as_dict()` 形状）。

    取不到返回 None —— 引擎据此报降级，**不要在这里造一个空构件集**，
    那会让「没识别」看起来像「识别出零个」。
    """
    if (ctx.file_ext or "").lower() != "pdf":
        logger.debug("合理性取构件跳过：%s 不是 PDF", ctx.file_ext)
        return None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_recognize, ctx.file_key, ctx.discipline,
                                 str(ctx.drawing_id), ctx.title)
            return future.result(timeout=RECOGNIZE_TIMEOUT_SEC)
    except concurrent.futures.TimeoutError:
        logger.warning("合理性取构件超时（>%ss）：drawing_id=%s",
                       RECOGNIZE_TIMEOUT_SEC, ctx.drawing_id)
    except Exception as exc:  # noqa: BLE001 - 增强能力失败不拖垮审图，但必须留痕
        logger.warning("合理性取构件失败：drawing_id=%s %s: %s",
                       ctx.drawing_id, type(exc).__name__, exc)
    return None
