"""VecFormerBackend 占位 stub 测试（C-10）。

验证占位契约：权重未释放期 ``is_available()`` 为 False、``spot`` 返回空且不抛异常，
并满足共享 ``SpottingBackend`` Protocol —— 保证一旦权重释放可无缝切换。
"""
from __future__ import annotations

from core.model3d.preprocess.schema import PrimitiveDoc
from core.model3d.spotting.types import SpottingBackend, SpottingResult
from core.model3d.spotting.vecformer import VecFormerBackend


def test_name_is_vecformer() -> None:
    assert VecFormerBackend().name == "vecformer"


def test_is_available_false_while_weights_unreleased() -> None:
    # Arrange / Act / Assert
    assert VecFormerBackend().is_available() is False


def test_spot_returns_empty_result_without_raising() -> None:
    # Arrange
    backend = VecFormerBackend()
    doc = PrimitiveDoc()

    # Act
    result = backend.spot(doc)

    # Assert
    assert isinstance(result, SpottingResult)
    assert result.backend == "vecformer"
    assert result.candidates == ()
    assert result.warnings == ("VecFormer 权重未释放，占位",)


def test_satisfies_spotting_backend_protocol() -> None:
    # runtime_checkable Protocol：占位后端须与 CADTransformer/mock 可互换
    assert isinstance(VecFormerBackend(), SpottingBackend)
