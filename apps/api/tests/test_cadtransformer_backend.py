"""C-08 CADTransformer 推理封装测试（无 torch/dgl 依赖下必须通过）。

覆盖：
- ``is_available()`` 在缺依赖 / 缺权重时返回 False；
- adapter 输入适配（``PrimitiveDoc`` → SVG/节点序列）与输出解析
  （逐图元预测 → 按实例聚合的 ``SymbolCandidate``）——合成数据，纯函数；
- ``spot()`` 不可用时优雅返回空 ``SpottingResult`` + warning，不抛异常，满足 Protocol。
"""
from __future__ import annotations

import os
from unittest import mock

import pytest

from core.model3d.preprocess.schema import Primitive, PrimitiveDoc
from core.model3d.spotting.cadtransformer import (
    CADTransformerBackend,
    build_model_input,
    map_class_name,
    parse_predictions,
)
from core.model3d.spotting.cadtransformer.adapter import NodePrediction
from core.model3d.spotting.types import SpottingBackend, SpottingResult


# --------------------------------------------------------------------------
# Fixtures：合成 PrimitiveDoc
# --------------------------------------------------------------------------
@pytest.fixture
def sample_doc() -> PrimitiveDoc:
    """两个符号：一扇门（矩形）+ 一段墙（线），另加一个背景图元。"""
    return PrimitiveDoc(
        page_w=100.0,
        page_h=80.0,
        primitives=(
            Primitive(id=1, type="rect", points=((10, 10), (30, 10), (30, 30), (10, 30)), layer="A-DOOR"),
            Primitive(id=2, type="line", points=((0, 40), (100, 40)), layer="A-WALL"),
            Primitive(id=3, type="line", points=((5, 5), (6, 6)), layer="MISC"),
        ),
    )


@pytest.fixture
def empty_doc() -> PrimitiveDoc:
    return PrimitiveDoc()


# --------------------------------------------------------------------------
# is_available() 降级逻辑
# --------------------------------------------------------------------------
def test_is_available_false_when_deps_missing(sample_doc):
    """dgl / torch-geometric 未安装 → 不可用（即便给了权重路径）。"""
    backend = CADTransformerBackend(weights_path="/nonexistent/weights.pth")
    assert backend.is_available() is False


def test_is_available_false_when_weights_missing():
    """无权重路径 → 不可用（即便依赖齐全，用 mock 令依赖探测通过）。"""
    backend = CADTransformerBackend(weights_path=None)
    with mock.patch(
        "core.model3d.spotting.cadtransformer.backend._deps_present",
        return_value=True,
    ):
        assert backend.is_available() is False


def test_is_available_false_when_weights_path_not_a_file(tmp_path):
    """权重路径指向不存在的文件 → 不可用。"""
    backend = CADTransformerBackend(weights_path=str(tmp_path / "missing.pth"))
    with mock.patch(
        "core.model3d.spotting.cadtransformer.backend._deps_present",
        return_value=True,
    ):
        assert backend.is_available() is False


def test_is_available_true_when_deps_and_weights_ready(tmp_path):
    """依赖齐全 + 权重文件存在 → 可用。"""
    weights = tmp_path / "floorplancad.pth"
    weights.write_bytes(b"fake-weights")
    backend = CADTransformerBackend(weights_path=str(weights))
    with mock.patch(
        "core.model3d.spotting.cadtransformer.backend._deps_present",
        return_value=True,
    ):
        assert backend.is_available() is True


def test_reads_weights_path_from_env(tmp_path):
    """未显式传参时从 CADTRANSFORMER_WEIGHTS 环境变量读取。"""
    weights = tmp_path / "w.pth"
    weights.write_bytes(b"x")
    with mock.patch.dict(os.environ, {"CADTRANSFORMER_WEIGHTS": str(weights)}):
        backend = CADTransformerBackend()
        assert backend._weights_path == str(weights)


# --------------------------------------------------------------------------
# Protocol 合规
# --------------------------------------------------------------------------
def test_backend_satisfies_protocol():
    backend = CADTransformerBackend()
    assert isinstance(backend, SpottingBackend)
    assert backend.name == "cadtransformer"


# --------------------------------------------------------------------------
# spot() 降级路径
# --------------------------------------------------------------------------
def test_spot_returns_empty_with_warning_when_unavailable(sample_doc):
    backend = CADTransformerBackend()  # 无依赖/权重 → 不可用
    result = backend.spot(sample_doc)
    assert isinstance(result, SpottingResult)
    assert result.backend == "cadtransformer"
    assert result.candidates == ()
    assert len(result.warnings) == 1
    assert "降级" in result.warnings[0]


def test_spot_never_raises_on_empty_doc(empty_doc):
    backend = CADTransformerBackend()
    result = backend.spot(empty_doc)
    assert result.candidates == ()
    assert result.warnings


def test_spot_degrades_when_inference_raises(sample_doc, tmp_path):
    """可用但推理抛异常 → 捕获并降级为空 + warning，绝不冒泡。"""
    weights = tmp_path / "w.pth"
    weights.write_bytes(b"x")
    backend = CADTransformerBackend(weights_path=str(weights))
    with mock.patch.object(backend, "is_available", return_value=True), mock.patch.object(
        backend, "_infer", side_effect=RuntimeError("boom")
    ):
        result = backend.spot(sample_doc)
    assert result.candidates == ()
    assert any("异常" in w for w in result.warnings)


# --------------------------------------------------------------------------
# adapter：输入适配 PrimitiveDoc → SVG + 节点序列
# --------------------------------------------------------------------------
def test_build_model_input_produces_svg_and_nodes(sample_doc):
    model_input = build_model_input(sample_doc)
    assert model_input.svg.startswith("<svg")
    assert "data-layer=\"A-DOOR\"" in model_input.svg
    assert model_input.node_count == 3


def test_build_model_input_normalizes_nodes_to_unit_domain(sample_doc):
    """节点坐标应落在归一化域内（等比缩放到 [0,1]），并保留图层/块元数据。"""
    model_input = build_model_input(sample_doc)
    for node in model_input.nodes:
        for x, y in node.points:
            assert -1e-6 <= x <= 1.0 + 1e-6
            assert -1e-6 <= y <= 1.0 + 1e-6
    door = next(n for n in model_input.nodes if n.primitive_id == 1)
    assert door.layer == "A-DOOR"
    assert door.ptype == "rect"
    assert model_input.normalize.scale > 0


def test_build_model_input_empty_doc():
    model_input = build_model_input(PrimitiveDoc())
    assert model_input.node_count == 0
    assert model_input.svg.startswith("<svg")


# --------------------------------------------------------------------------
# adapter：类名映射
# --------------------------------------------------------------------------
def test_map_class_name_variants():
    assert map_class_name("single door") == "door"
    assert map_class_name("Sliding_Door") == "door"      # 大小写/下划线归一化
    assert map_class_name("bay window") == "window"
    assert map_class_name("curtain wall") == "wall"
    assert map_class_name("toilet") == "equipment"
    assert map_class_name("background") is None
    assert map_class_name("unknown class xyz") is None


# --------------------------------------------------------------------------
# adapter：输出解析 预测 → SymbolCandidate
# --------------------------------------------------------------------------
def test_parse_predictions_groups_by_instance(sample_doc):
    predictions = [
        NodePrediction(primitive_id=1, class_name="single door", confidence=0.9, instance_id=100),
        NodePrediction(primitive_id=2, class_name="wall", confidence=0.8, instance_id=101),
    ]
    candidates = parse_predictions(sample_doc, predictions)
    by_cat = {c.category: c for c in candidates}
    assert set(by_cat) == {"door", "wall"}
    door = by_cat["door"]
    assert door.source == "model"
    assert door.primitive_ids == (1,)
    # rect 图元 (10,10)-(30,30) 的 bbox（原始页面坐标）
    assert door.bbox == (10.0, 10.0, 30.0, 30.0)
    assert door.evidence["model"] == "cadtransformer"


def test_parse_predictions_merges_multi_primitive_instance(sample_doc):
    """同一 instance_id 的多个图元聚为一个候选，bbox 取并集、置信取均值。"""
    predictions = [
        NodePrediction(primitive_id=1, class_name="single door", confidence=1.0, instance_id=7),
        NodePrediction(primitive_id=2, class_name="single door", confidence=0.6, instance_id=7),
    ]
    candidates = parse_predictions(sample_doc, predictions)
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.category == "door"
    assert cand.primitive_ids == (1, 2)
    assert cand.confidence == pytest.approx(0.8)  # (1.0 + 0.6) / 2
    # 并集：rect(10..30) ∪ line(0,40)-(100,40)
    assert cand.bbox == (0.0, 10.0, 100.0, 40.0)


def test_parse_predictions_drops_background_and_unassigned(sample_doc):
    predictions = [
        NodePrediction(primitive_id=3, class_name="background", confidence=0.9, instance_id=1),
        NodePrediction(primitive_id=1, class_name="single door", confidence=0.9, instance_id=-1),
        NodePrediction(primitive_id=2, class_name="unknown xyz", confidence=0.9, instance_id=2),
    ]
    candidates = parse_predictions(sample_doc, predictions)
    assert candidates == []


def test_parse_predictions_majority_vote_on_class(sample_doc):
    """实例内类别分歧时取多数票。"""
    predictions = [
        NodePrediction(primitive_id=1, class_name="single door", confidence=0.9, instance_id=5),
        NodePrediction(primitive_id=2, class_name="single door", confidence=0.9, instance_id=5),
        NodePrediction(primitive_id=3, class_name="window", confidence=0.9, instance_id=5),
    ]
    candidates = parse_predictions(sample_doc, predictions)
    assert len(candidates) == 1
    assert candidates[0].category == "door"


def test_parse_predictions_clamps_confidence(sample_doc):
    predictions = [
        NodePrediction(primitive_id=1, class_name="single door", confidence=1.7, instance_id=9),
    ]
    candidates = parse_predictions(sample_doc, predictions)
    assert candidates[0].confidence == 1.0


def test_parse_predictions_empty_list(sample_doc):
    assert parse_predictions(sample_doc, []) == []
