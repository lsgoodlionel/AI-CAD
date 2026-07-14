"""PaddleOCR-VL 适配器占位（D-16，未装依赖，未接线真实推理）。

背景：PaddleOCR-VL（PP-StructureV3，Apache-2.0）在 OmniDocBench 上显著优于
现役 PP-OCR 2.x 系（见 ``docs/PHASE_D_LANE5_PLAN.md`` D-16 节），适合做「图签/
说明页整页结构化」。是否切换需先跑离线评测基座（``core/model3d/ocr/eval/``）
量化对比，而不是直接换默认后端。

本文件只预写 ``OcrBackend`` Protocol 的**契约形状**，让评测 harness 能把
「未来的 VL 后端」和「现有 paddle/rapid/mock」放进同一张对比表——即便 VL
当前恒不可用，报告里也能如实显示一行「paddleocr_vl：不可用」，而不是让
调用方手写 if/else 特殊处理。

**本次改动明确不做**：
  - 不装 paddleocr/paddlepaddle（沿用 ``requirements-ocr.txt`` 现状，镜像
    是否装 3.x 是另一个待确认项，见 D-16 节「需你提供」①）。
  - 不接线真实推理调用（PaddleOCR-VL 的具体 pipeline 类名/构造参数/输出
    结构截至本次改动未核对，不臆造签名假装跑通）。
  - **不修改** ``ocr/service.py`` 的默认回退顺序（paddle→rapid→mock）——
    本后端不在该回退链里，只在评测 harness 中被显式实例化、显式对比。

真实接线点：``_load_engine()`` 内 TODO 处。届时按当时发布的 PaddleOCR-VL /
PP-StructureV3 API 文档核实类名与调用方式，参照 ``paddle_backend.py`` 的
``_construct_paddleocr`` / ``parse_paddle_output`` 范式实现「构造 → 推理 →
解析为 RawBox 列表」，并把 ``recognize()`` 里的占位 ``return []`` 换成真实
解析结果。``is_available()`` 无需改动——依赖具备 + 引擎构造成功即自动为 True。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_engine_singleton = None
_load_failed = False

RawBox = tuple[str, tuple[float, float, float, float], float]


def _load_engine():
    """懒加载 PaddleOCR-VL 引擎；依赖缺失或未接线一律返回 None（优雅降级）。

    TODO(D-16 真实接线，镜像装好 paddleocr>=3.x 后填充)：
      1. 按当时 paddleocr 发布的 PaddleOCR-VL / PP-StructureV3 API 核实构造
         方式（类名/参数未在本次改动中核对，禁止臆造）。
      2. 构造成功后缓存到 ``_engine_singleton`` 并返回，而不是像现在这样
         探测到依赖存在也仍强制返回 None。
    """
    global _engine_singleton, _load_failed
    if _engine_singleton is not None:
        return _engine_singleton
    if _load_failed:
        return None
    try:
        import paddleocr  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — 未安装/加载失败一律降级
        logger.info("[ocr.paddleocr_vl] paddleocr 未安装，降级: %s", exc)
        _load_failed = True
        return None
    # 依赖已探测到，但 VL 推理管线尚未接线（见上方 TODO）——显式保持不可用，
    # 不假装能跑。真实接线时把下面两行替换为真实构造逻辑。
    logger.info("[ocr.paddleocr_vl] paddleocr 依赖已就绪，但 VL 管线未接线（stub，见模块 TODO）")
    _load_failed = True
    return None


class PaddleOcrVlBackend:
    """PaddleOCR-VL（PP-StructureV3）适配器占位，实现 ``OcrBackend`` Protocol。

    当前 ``is_available()`` 恒为 False（真实推理待接线，见模块 docstring）；
    ``recognize()`` 在不可用时返回空列表 + 告警，绝不抛错阻断评测/建模链路
    ——与 ``PaddleOcrBackend`` / ``RapidOcrBackend`` 同一降级范式。
    """

    name = "paddleocr_vl"

    def is_available(self) -> bool:
        return _load_engine() is not None

    def recognize(self, image_rgb, warnings: list[str]) -> list[RawBox]:
        engine = _load_engine()
        if engine is None:
            warnings.append("paddleocr_vl 不可用（stub 未接线真实推理，见模块 TODO）")
            return []
        # 真实推理接线点：待 _load_engine 返回非 None 时，按 PP-StructureV3
        # 输出结构解析为 RawBox 列表（参照 paddle_backend.parse_paddle_output）。
        return []
