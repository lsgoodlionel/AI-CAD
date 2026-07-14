"""D-09 单测 —— spotting 融合回灌构件识别（``services/model_elements.py``）。

覆盖 Phase D 泳道 3 验收核心：
    ① 无真实 spotting 后端（仅 mock 兜底）—— 融合回灌为空操作，几何/数量完全不变，
       仅补 source="rule" + confidence 标注（纯规则路径完整回退）。
    ② 有真实模型信号、同处同类 —— 共识增强，规则构件原地打 source="fused"，几何不变。
    ③ 有真实模型信号、同处异类、规则强命中 —— 规则不被覆盖（source 仍为 "rule"）。
    ④ 模型候选落在规则空白区 —— 补召回为新构件条目，source="model"。
    ⑤ 无构件坐标系（envelope 缺失）—— 优雅降级为标注-only。

不依赖真实 CADTransformer/GPU：通过注入假 ``SpottingBackend`` 驱动“有真实模型信号”分支，
默认环境（无 torch/权重）天然只剩 mock，直接验证「无 spotting 后端」分支。
"""
from __future__ import annotations

import pytest

import services.model_elements as model_elements
from core.model3d.spotting.types import SpottingResult, SymbolCandidate
from core.model3d.types import DrawingGeometry


# ── 测试辅助 ─────────────────────────────────────────────
class _FakeBackend:
    """可控符号候选的假后端（非 mock，触发真实融合分支）。"""

    name = "fake-model"

    def __init__(self, candidates: tuple[SymbolCandidate, ...]):
        self._candidates = candidates

    def is_available(self) -> bool:
        return True

    def spot(self, doc) -> SpottingResult:
        return SpottingResult(candidates=self._candidates, backend=self.name)


def _service_with(candidates: tuple[SymbolCandidate, ...]):
    from core.model3d.spotting.service import SpottingService

    return SpottingService(backends=[_FakeBackend(candidates)])


def _geom(page_w: float = 100.0, page_h: float = 100.0) -> DrawingGeometry:
    """最小可用几何（供 preprocess_geometry 产出非空 PrimitiveDoc）。"""
    return DrawingGeometry(
        page_w=page_w, page_h=page_h,
        rects=[(10.0, 10.0, 5.0, 5.0, True)],
        rect_layers=["A-COLS"], rect_blocks=[""],
    )


def _column_elements() -> dict[str, list]:
    """单个规则柱构件（米坐标 0..2 方框），envelope == 该柱 bbox。"""
    return {
        "columns": [{"outline": [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]], "src": "d1"}],
        "walls": [], "beams": [], "slabs": [], "pipes": [], "equipment": [],
    }


@pytest.fixture(autouse=True)
def _clear_spotting_singleton():
    """每个用例前清空 lru_cache 单例，避免跨用例复用打过桩的 SpottingService。"""
    model_elements._spotting_service.cache_clear()
    yield
    model_elements._spotting_service.cache_clear()


# ── ① 无真实后端（仅 mock）—— 纯规则回退 ─────────────────
def test_no_real_backend_falls_back_to_rule_tagging_only(monkeypatch):
    # Arrange: 默认环境（无 torch/权重）SpottingService 只剩 mock 兜底
    elements = _column_elements()
    original_outline = elements["columns"][0]["outline"]

    # Act
    result = model_elements._reinject_fusion(elements, _geom(), "d1")

    # Assert: 几何/数量完全不变，仅补 source/confidence
    assert len(result["columns"]) == 1
    col = result["columns"][0]
    assert col["outline"] == original_outline
    assert col["source"] == "rule"
    assert col["confidence"] == pytest.approx(model_elements._RULE_CONFIDENCE)
    # 其余类别仍为空列表（数量不变）
    assert result["walls"] == [] and result["pipes"] == []


def test_spot_model_candidates_empty_when_only_mock_available():
    # Arrange / Act
    candidates = model_elements._spot_model_candidates(_geom(), "d1")
    # Assert
    assert candidates == ()


# ── ② 同处同类 —— 共识增强，几何不变 ─────────────────────
def test_consensus_tags_fused_source_without_changing_geometry(monkeypatch):
    # Arrange: 模型候选与规则柱同处同类，置信 0.8
    model_cand = (
        SymbolCandidate(category="column", confidence=0.8, bbox=(0.0, 0.0, 100.0, 100.0), source="model"),
    )
    monkeypatch.setattr(model_elements, "_spotting_service", lambda: _service_with(model_cand))
    elements = _column_elements()
    original_outline = elements["columns"][0]["outline"]

    # Act
    result = model_elements._reinject_fusion(elements, _geom(), "d1")

    # Assert: 几何完全不变，source 升级为 fused，置信被增强（> 规则原始置信）
    assert len(result["columns"]) == 1
    col = result["columns"][0]
    assert col["outline"] == original_outline
    assert col["source"] == "fused"
    assert col["confidence"] > model_elements._RULE_CONFIDENCE


# ── ③ 同处异类 + 规则强命中 —— 规则不被覆盖 ──────────────
def test_strong_rule_not_overridden_by_conflicting_model(monkeypatch):
    # Arrange: 模型候选同处但类别冲突（wall），规则置信 0.92 ≥ 强命中门槛 0.85
    model_cand = (
        SymbolCandidate(category="wall", confidence=0.95, bbox=(0.0, 0.0, 100.0, 100.0), source="model"),
    )
    monkeypatch.setattr(model_elements, "_spotting_service", lambda: _service_with(model_cand))
    elements = _column_elements()
    original_outline = elements["columns"][0]["outline"]

    # Act
    result = model_elements._reinject_fusion(elements, _geom(), "d1")

    # Assert: 仍是唯一一个柱构件，source 保持 rule，几何/置信不变（强规则保护）
    assert len(result["columns"]) == 1
    assert result["walls"] == []
    col = result["columns"][0]
    assert col["outline"] == original_outline
    assert col["source"] == "rule"
    assert col["confidence"] == pytest.approx(model_elements._RULE_CONFIDENCE)


# ── ④ 模型补召回 —— 空白区落盘为新构件 ───────────────────
def test_model_recall_adds_new_element_in_gap_region(monkeypatch):
    # Arrange: 模型候选落在规则空白区（不与已有柱重叠），置信高于补召回门槛 0.45
    model_cand = (
        SymbolCandidate(category="pipe", confidence=0.8, bbox=(50.0, 50.0, 60.0, 60.0), source="model"),
    )
    monkeypatch.setattr(model_elements, "_spotting_service", lambda: _service_with(model_cand))
    elements = _column_elements()

    # Act
    result = model_elements._reinject_fusion(elements, _geom(), "d1")

    # Assert: 原有柱不受影响；新增一条 source="model" 的管线
    assert len(result["columns"]) == 1
    assert result["columns"][0]["source"] == "rule"
    assert len(result["pipes"]) == 1
    pipe = result["pipes"][0]
    assert pipe["source"] == "model"
    assert pipe["confidence"] == pytest.approx(0.8)
    assert "path" in pipe and len(pipe["path"]) == 2


def test_model_recall_below_confidence_threshold_rejected(monkeypatch):
    # Arrange: 模型候选置信低于门槛（默认 0.45）
    model_cand = (
        SymbolCandidate(category="pipe", confidence=0.2, bbox=(50.0, 50.0, 60.0, 60.0), source="model"),
    )
    monkeypatch.setattr(model_elements, "_spotting_service", lambda: _service_with(model_cand))
    elements = _column_elements()

    # Act
    result = model_elements._reinject_fusion(elements, _geom(), "d1")

    # Assert: 噪声不补召回
    assert result["pipes"] == []


def test_model_recall_skips_categories_without_scene_kind(monkeypatch):
    # Arrange: 模型候选类别（door）尚未纳入 scene 构件类别，不应臆造条目
    model_cand = (
        SymbolCandidate(category="door", confidence=0.9, bbox=(50.0, 50.0, 60.0, 60.0), source="model"),
    )
    monkeypatch.setattr(model_elements, "_spotting_service", lambda: _service_with(model_cand))
    elements = _column_elements()

    # Act
    result = model_elements._reinject_fusion(elements, _geom(), "d1")

    # Assert: 无任何类别新增条目（door 无对应 scene kind，安全跳过）
    total = sum(len(v) for v in result.values())
    assert total == 1  # 仅原有柱


# ── ⑤ 无构件坐标系 —— 优雅降级为标注-only ────────────────
def test_no_envelope_falls_back_to_tagging_only(monkeypatch):
    # Arrange: 有真实模型信号，但本图未识别出任何构件（无坐标系可用于配准）
    model_cand = (
        SymbolCandidate(category="pipe", confidence=0.8, bbox=(0.0, 0.0, 10.0, 10.0), source="model"),
    )
    monkeypatch.setattr(model_elements, "_spotting_service", lambda: _service_with(model_cand))
    empty_elements = {k: [] for k in model_elements.EMPTY_ELEMENTS}

    # Act
    result = model_elements._reinject_fusion(empty_elements, _geom(), "d1")

    # Assert: 无法配准 → 原样返回（此处即为空，未凭空捏造构件）
    assert result == empty_elements


# ── 融合失败降级 ─────────────────────────────────────────
def test_fusion_exception_falls_back_to_rule_tagging(monkeypatch):
    # Arrange: fuse() 抛异常时必须优雅降级，不能让构件识别主流程失败
    model_cand = (
        SymbolCandidate(category="pipe", confidence=0.8, bbox=(50.0, 50.0, 60.0, 60.0), source="model"),
    )
    monkeypatch.setattr(model_elements, "_spotting_service", lambda: _service_with(model_cand))

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("core.model3d.fusion.fuse", _boom)
    elements = _column_elements()
    original_outline = elements["columns"][0]["outline"]

    # Act
    result = model_elements._reinject_fusion(elements, _geom(), "d1")

    # Assert: 降级为纯规则 + 标注
    assert result["columns"][0]["outline"] == original_outline
    assert result["columns"][0]["source"] == "rule"


# ── 集成：_recognize_sync 携带 source/confidence，纯规则路径行为不变 ────
def test_recognize_sync_tags_elements_without_real_backend():
    # Arrange: 最小 DXF 字节流，触发真实 extract_dxf_geometry → recognize → 回灌
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5)], format="xy", close=True, dxfattribs={"layer": "A-COLS"}
    )
    import io

    buf = io.StringIO()
    doc.write(buf)
    data = buf.getvalue().encode("utf-8")

    # Act
    out = model_elements._recognize_sync(data, "dxf", "structure", "d1")

    # Assert: 只要识别出构件，均带 source/confidence（无真实后端时 source 恒为 rule）
    if out is None:
        pytest.skip("最小 DXF 未产出可识别几何（环境 ezdxf 行为差异），非本用例断言目标")
    for items in out["elements"].values():
        for item in items:
            assert item["source"] == "rule"
            assert item["confidence"] == pytest.approx(model_elements._RULE_CONFIDENCE)
