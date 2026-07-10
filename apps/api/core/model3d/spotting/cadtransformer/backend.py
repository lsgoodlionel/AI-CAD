"""CADTransformer(MIT) 推理后端封装（C-08 PoC 基线脚手架）。

实现 ``SpottingBackend`` Protocol。**严格约束**：

1. torch / dgl / torch-geometric **一律懒加载**（函数内 import，模块顶层绝不 import），
   使无这些依赖的环境（CI、本地无 GPU）也能 import 本模块。
2. ``is_available()`` 检测「依赖 + 权重」是否就绪，缺任一 → False；服务据此降级到 mock。
3. ``spot()`` 不可用时返回空 ``SpottingResult`` + warning；就绪时真实推理路径用
   try/except 兜底，**绝不跨边界抛异常、绝不在 CI 崩**。

真实权重（FloorPlanCAD，PQ≈68.5）下的运行方式见同目录 README。本环境无 GPU/权重，
``is_available()`` 恒为 False，走降级路径。
"""
from __future__ import annotations

import importlib.util
import logging
import os

from core.model3d.preprocess.schema import PrimitiveDoc

from ..types import SpottingResult
from .adapter import NodePrediction, build_model_input, parse_predictions

logger = logging.getLogger(__name__)

# 真实推理所需的第三方依赖（均较老，见 requirements-spotting.txt 锁版本）
_REQUIRED_DEPS = ("torch", "dgl", "torch_geometric")

# 权重路径环境变量（官方 FloorPlanCAD 权重，MIT）
_WEIGHTS_ENV = "CADTRANSFORMER_WEIGHTS"
_DEVICE_ENV = "CADTRANSFORMER_DEVICE"

_UNAVAILABLE_WARNING = "CADTransformer 权重/依赖未就绪，降级"


def _deps_present() -> bool:
    """仅探测依赖是否可导入（``find_spec`` 不会真正 import，避免副作用）。"""
    for dep in _REQUIRED_DEPS:
        try:
            if importlib.util.find_spec(dep) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def _weights_ready(weights_path: str | None) -> bool:
    return bool(weights_path) and os.path.isfile(weights_path)


class CADTransformerBackend:
    """CADTransformer 推理后端。缺依赖/权重时优雅降级到不可用。"""

    name = "cadtransformer"

    def __init__(
        self,
        weights_path: str | None = None,
        device: str | None = None,
    ) -> None:
        # 显式入参优先，否则回落到环境变量（部署期注入，不硬编码）
        self._weights_path = weights_path or os.environ.get(_WEIGHTS_ENV)
        self._device = device or os.environ.get(_DEVICE_ENV, "cpu")
        self._model = None  # 懒加载缓存

    # -- 能力探测 ---------------------------------------------------------
    def is_available(self) -> bool:
        """依赖（torch/dgl/torch-geometric）与权重文件均就绪才可用。"""
        if not _deps_present():
            return False
        if not _weights_ready(self._weights_path):
            return False
        return True

    # -- 推理主入口 -------------------------------------------------------
    def spot(self, doc: PrimitiveDoc) -> SpottingResult:
        """图元文档 → 符号候选。不可用/异常一律降级为空结果 + warning。"""
        if not self.is_available():
            return SpottingResult(
                backend=self.name,
                warnings=(_UNAVAILABLE_WARNING,),
            )
        try:
            model_input = build_model_input(doc)
            raw = self._infer(model_input)
            predictions = self._to_predictions(raw)
            candidates = parse_predictions(
                doc,
                predictions,
                backend_name=self.name,
                weights_id=os.path.basename(self._weights_path or ""),
            )
            return SpottingResult(candidates=tuple(candidates), backend=self.name)
        except Exception as exc:  # noqa: BLE001 — 推理失败绝不跨边界抛出
            logger.warning("[spotting.cadtransformer] 推理失败，降级: %s", exc)
            return SpottingResult(
                backend=self.name,
                warnings=(f"CADTransformer 推理异常，降级: {exc}",),
            )

    # -- 真实推理路径（依赖 torch/dgl，仅在 is_available() 为真时执行）-----
    def _load_model(self):
        """懒加载权重（torch 在此才 import）。缓存到实例。"""
        if self._model is not None:
            return self._model
        import torch  # noqa: F401 — 懒加载：仅在真实推理路径导入

        # NOTE(C-08 PoC): 官方 CADTransformer 模型定义随上游仓库引入（vendored/子模块）。
        # 此处仅占位加载流程，真实接线在具备权重与 GPU 的环境完成（见 README）。
        checkpoint = torch.load(self._weights_path, map_location=self._device)
        self._model = checkpoint
        return self._model

    def _infer(self, model_input) -> object:
        """执行前向推理，返回上游原始输出。

        真实实现：图元序列 → dgl 图 → CADTransformer 前向 → 逐节点 logits + 实例分组。
        本 PoC 脚手架保留接口，真实接线在具备依赖/权重的环境完成。
        """
        model = self._load_model()  # noqa: F841 — 保留加载副作用与缓存
        # 真实推理在具备 torch/dgl/权重的 GPU 环境接线（见 README「GPU 运行方式」）。
        raise NotImplementedError(
            "CADTransformer 前向推理需在具备 torch/dgl/GPU 与官方权重的环境接线"
        )

    @staticmethod
    def _to_predictions(raw: object) -> list[NodePrediction]:
        """上游原始输出 → ``NodePrediction`` 列表（供 adapter 聚合）。

        真实实现：解析逐节点类别 logits（argmax + softmax 置信）与实例分组头。
        """
        # 真实输出解析在接线阶段实现；脚手架阶段不会走到此处（_infer 先抛出）。
        return []
