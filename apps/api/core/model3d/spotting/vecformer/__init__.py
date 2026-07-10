"""VecFormer 符号识别后端 —— 预留迁移槽（C-10，权重待释放）。

VecFormer（Apache 2.0，FloorPlanCAD PQ 91.1）是中期精度升级目标，**代码许可可商用**
但**官方权重暂未释放**（见 ``docs/PHASE_C_LICENSE_AUDIT.md`` §3.2、``docs/PHASE_C_VECFORMER_WATCH.md``）。

本模块是**占位 stub**：实现共享契约 ``SpottingBackend`` Protocol（与 C-08 CADTransformer、
mock 后端同缝），使 C-12 SpottingService 一旦权重释放即可无缝切换后端，无需改服务契约。

当前纪律：
- ``is_available()`` 恒为 ``False``（权重未释放，服务自动降级到 mock/CADTransformer）。
- ``spot()`` 返回空 ``SpottingResult`` 并附警告，**绝不抛异常、绝不 import torch 或任何未装依赖**。
- 合规门禁：权重释放 ≠ 自动放行——须先复核「权重是否随 Apache 2.0 或另附非商用条款」
  （对齐 SymPoint 教训：代码许可与权重许可可能不一致），复核通过方可进产品。
"""
from __future__ import annotations

from core.model3d.preprocess.schema import PrimitiveDoc

from ..types import SpottingResult

_WEIGHTS_UNRELEASED_WARNING = "VecFormer 权重未释放，占位"


class VecFormerBackend:
    """VecFormer(Apache 2.0) 后端占位实现，等待权重释放后填充真实推理。

    实现 ``SpottingBackend`` Protocol（``name`` / ``is_available`` / ``spot``），
    与 CADTransformer(C-08)、MockSpottingBackend 可互换，接入 C-12 SpottingService
    的后端选择逻辑。真实推理待权重释放 + 权重许可复核通过后，在 ``spot`` 内接入
    （届时按 Apache 2.0 义务保留 ``LICENSE`` / ``NOTICE`` 并标注对上游文件的修改）。
    """

    name = "vecformer"

    def is_available(self) -> bool:
        """权重/依赖是否就绪。占位期恒为 False —— 权重未释放，不可服务化。"""
        return False

    def spot(self, doc: PrimitiveDoc) -> SpottingResult:  # noqa: ARG002 — 占位，暂不消费 doc
        """占位实现：返回空候选 + 警告，绝不抛异常。

        权重释放并通过许可复核后，此处接入真实推理（``PrimitiveDoc`` → SVG/图元序列
        适配 → VecFormer 前向 → ``SymbolCandidate``），保持返回契约不变。
        """
        return SpottingResult(
            backend=self.name,
            warnings=(_WEIGHTS_UNRELEASED_WARNING,),
        )
