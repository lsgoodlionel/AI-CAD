"""D-10：OCR 三馈线中「axis_anchors → 跨图配准」「space_labels → 语义树」的接线测试。

``elevation_candidates`` 早于本块已接（section_z/model_story），本文件只覆盖新接的
两条：
  - ``axis_anchors`` → ``services/cross_view_registration.py``
  - ``space_labels`` → ``services/model_semantics.py``

统一验证「置信门槛 + 几何/正则优先、OCR 只补缺不覆盖 + 绝不虚高置信」的纪律。
"""
from __future__ import annotations

import pytest

from core.model3d.elevation_opening_extractor import ElevationOpenings
from core.model3d.grid_anchor_extractor import GridAxis, GridSystem
from core.model3d.ocr import consume
from core.model3d.ocr.types import OcrResult, TextToken
from core.model3d.section_level_extractor import LevelMark, SectionLevels
from services.cross_view_registration import (
    ElevationView,
    SectionView,
    register_views,
)
from services.model_semantics import (
    build_semantic_graph,
    ocr_space_label_candidates,
)


# ── 测试构造辅助 ─────────────────────────────────────────────────

def _plan_grid(x_labels: list[tuple[str, float]]) -> GridSystem:
    axes_x = tuple(GridAxis(label=label, coord=coord) for label, coord in x_labels)
    return GridSystem(axes_x=axes_x, axes_y=(), confidence=1.0, unlabeled=False)


def _section_view(drawing_id: str, x_labels: list[tuple[str, float]], ocr_anchors=()) -> SectionView:
    marks = (LevelMark(elevation_m=0.0, label="±0.000", confidence=0.9, source_ref={}),)
    axes_x = tuple(GridAxis(label=label, coord=coord) for label, coord in x_labels)
    return SectionView(
        drawing_id=drawing_id,
        grid=GridSystem(axes_x=axes_x, axes_y=(), confidence=1.0, unlabeled=False),
        levels=SectionLevels(marks=marks, reason=None, fit={}),
        ocr_axis_anchors=tuple(ocr_anchors),
    )


# ── axis_anchors → cross_view_registration ──────────────────────


@pytest.mark.unit
def test_ocr_axis_anchor_fills_missing_label_without_overriding_geometry():
    """OCR 给出与几何冲突的同名标签 "1" 应被忽略；缺失的字母标签 "A" 应被采纳。"""
    plan = _plan_grid([("1", 100.0)])
    ocr_anchors = [
        {"label": "1", "center": (999.0, 999.0), "bbox": [0, 0, 1, 1], "confidence": 0.99},
        {"label": "A", "center": (0.0, 250.0), "bbox": [0, 0, 1, 1], "confidence": 0.9},
    ]

    reg = register_views(plan, [], [], plan_ocr_anchors=ocr_anchors)

    assert reg.axis_map["1"] == pytest.approx(100.0)
    assert reg.axis_label_sources["1"] == "geometry"
    assert reg.axis_map["A"] == pytest.approx(250.0)
    assert reg.axis_label_sources["A"] == "ocr"


@pytest.mark.unit
def test_ocr_axis_anchor_below_confidence_threshold_dropped():
    """OCR 轴号锚点置信低于配准门槛（0.75）时不采信——读错比缺失更糟。"""
    plan = _plan_grid([("1", 100.0)])
    ocr_anchors = [{"label": "B", "center": (0.0, 10.0), "confidence": 0.5}]

    reg = register_views(plan, [], [], plan_ocr_anchors=ocr_anchors)

    assert "B" not in reg.axis_map
    assert "B" not in reg.axis_label_sources


@pytest.mark.unit
def test_ocr_axis_anchor_missing_label_and_center_ignored_gracefully():
    """缺 label / center 的畸形锚点不应崩，只是被跳过。"""
    plan = _plan_grid([("1", 100.0)])
    ocr_anchors = [{"confidence": 0.9}, {"label": "", "confidence": 0.9}]

    reg = register_views(plan, [], [], plan_ocr_anchors=ocr_anchors)

    assert reg.axis_map == {"1": 100.0}


@pytest.mark.unit
def test_ocr_axis_anchor_on_section_view_merges_through_offset_registration():
    """剖面自身 OCR 补的轴号，经与几何轴号相同的 offset 配准流程归入参考帧坐标系。"""
    plan = _plan_grid([("1", 100.0), ("2", 300.0)])
    section = _section_view(
        "sec1",
        x_labels=[("1", 110.0)],  # 与平面共有轴号 "1"，dx = 100-110 = -10
        ocr_anchors=[
            {"label": "1a", "center": (315.0, 0.0), "confidence": 0.85},
        ],
    )

    reg = register_views(plan, [section], [])

    assert reg.axis_map["1a"] == pytest.approx(305.0)  # 315 + dx(-10)
    assert reg.axis_label_sources["1a"] == "ocr"
    # 平面自身的 "1"/"2" 仍是几何来源，未被剖面 OCR 影响
    assert reg.axis_label_sources["1"] == "geometry"
    assert reg.axis_label_sources["2"] == "geometry"


@pytest.mark.unit
def test_ocr_axis_anchor_on_elevation_view_merges_through_offset_registration():
    plan = _plan_grid([("1", 100.0)])
    elevation = ElevationView(
        drawing_id="elev1",
        grid=GridSystem(
            axes_x=(GridAxis(label="1", coord=120.0),), axes_y=(), confidence=1.0, unlabeled=False
        ),
        openings=ElevationOpenings(openings=()),
        ocr_axis_anchors=({"label": "A", "center": (0.0, 40.0), "confidence": 0.8},),
    )

    reg = register_views(plan, [], [elevation])

    # 既有 _build_axis_map 逻辑对 axes_x / axes_y 统一套用 dx（不区分 dy）——
    # 这是接线前就有的既定行为，本测试锁定 OCR 补入的轴号走相同规则，不额外改动。
    # dx = ref["1"](100) - cur["1"](120) = -20 → "A": 40 + (-20) = 20
    assert reg.axis_map["A"] == pytest.approx(20.0)
    assert reg.axis_label_sources["A"] == "ocr"


@pytest.mark.unit
def test_axis_anchors_from_real_ocr_result_feeds_registration_end_to_end():
    """真实 ``consume.axis_anchors`` 输出直接喂 register_views，端到端接线验证。"""
    plan = _plan_grid([("1", 100.0)])
    ocr_result = OcrResult(
        tokens=(
            TextToken("A", (0.0, 400.0, 2.0, 402.0), 0.9, "axis"),
            TextToken("z", (0.0, 0.0, 2.0, 2.0), 0.5, "axis"),  # 低置信，consume 层已过滤
        ),
        backend="mock",
    )

    anchors = consume.axis_anchors(ocr_result)
    reg = register_views(plan, [], [], plan_ocr_anchors=anchors)

    assert reg.axis_map["A"] == pytest.approx(401.0)
    assert reg.axis_label_sources["A"] == "ocr"
    assert "z" not in reg.axis_map


@pytest.mark.unit
def test_register_views_backward_compatible_without_ocr_anchors():
    """未传 OCR 相关参数时行为与接线前完全一致（不破坏既有调用方）。"""
    plan = _plan_grid([("1", 100.0), ("2", 300.0)])
    section = _section_view("sec1", x_labels=[("1", 110.0), ("2", 310.0)])

    reg = register_views(plan, [section], [])

    assert reg.axis_map["1"] == pytest.approx(100.0)
    assert set(reg.axis_label_sources) == {"1", "2"}
    assert all(source == "geometry" for source in reg.axis_label_sources.values())


# ── space_labels → model_semantics ───────────────────────────────


@pytest.mark.unit
def test_ocr_room_name_candidate_confidence_never_inflated_above_cap():
    """room_name 置信封顶 0.8（与 drawing_semantics 正则命中同量级），高置信 OCR 读数不虚高。"""
    labels = [{"text": "会议室", "kind": "room_name", "center": (1.0, 1.0), "confidence": 0.99}]

    candidates = ocr_space_label_candidates(labels, drawing_id="d1")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.node_type == "functional_space"
    assert candidate.source == "ocr_room_name"
    assert candidate.confidence == pytest.approx(0.8)  # 封顶，不是 0.99


@pytest.mark.unit
def test_ocr_room_name_candidate_keeps_low_ocr_confidence_when_below_cap():
    labels = [{"text": "设备间", "kind": "room_name", "center": (0.0, 0.0), "confidence": 0.5}]

    candidates = ocr_space_label_candidates(labels, drawing_id="d1")

    assert candidates[0].confidence == pytest.approx(0.5)  # 低于封顶时如实反映 OCR 自身置信


@pytest.mark.unit
def test_ocr_title_candidate_reuses_drawing_semantics_patterns_with_source_rewritten():
    """title 文本复用 drawing_semantics 正则，置信取「正则置信」与「OCR 置信」较小者。"""
    labels = [{"text": "1#楼结构平面图", "kind": "title", "center": (0.0, 0.0), "confidence": 0.99}]

    candidates = ocr_space_label_candidates(labels, drawing_id="d1")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.node_type == "building_unit"
    assert candidate.source == "ocr_title"
    assert candidate.confidence == pytest.approx(0.95)  # 正则固定 0.95 < OCR 的 0.99，取较小者
    assert candidate.context["drawing_id"] == "d1"


@pytest.mark.unit
def test_ocr_title_candidate_capped_by_low_ocr_confidence():
    labels = [{"text": "1#楼结构平面图", "kind": "title", "center": (0.0, 0.0), "confidence": 0.4}]

    candidates = ocr_space_label_candidates(labels, drawing_id="d1")

    assert candidates[0].confidence == pytest.approx(0.4)  # OCR 自身置信更低，如实反映


@pytest.mark.unit
def test_ocr_level_name_not_mapped_to_any_canonical_semantic_node():
    """level_name 不落入本模块四类语义节点，显式跳过（预留给楼层链路）。"""
    labels = [{"text": "三层", "kind": "level_name", "center": (0.0, 0.0), "confidence": 0.9}]

    assert ocr_space_label_candidates(labels, drawing_id="d1") == []


@pytest.mark.unit
def test_ocr_space_label_candidates_skip_empty_text_or_zero_confidence():
    labels = [
        {"text": "", "kind": "room_name", "confidence": 0.9},
        {"text": "走廊", "kind": "room_name", "confidence": 0.0},
    ]

    assert ocr_space_label_candidates(labels, drawing_id="d1") == []


@pytest.mark.unit
def test_ocr_space_label_candidates_handles_none_input():
    assert ocr_space_label_candidates(None) == []


@pytest.mark.asyncio
async def test_build_semantic_graph_merges_ocr_space_labels_as_independent_source():
    """两独立来源（filename title 正则 + OCR room_name）汇入同一 functional_space 候选。"""

    class _DB:
        async def fetch_all(self, *args, **kwargs):
            return []

    drawing = {
        "id": "d1",
        "drawing_no": "M-001",
        "title": "会议室机电点位图",
        "ocr_space_labels": [
            {"text": "会议室", "kind": "room_name", "center": (0.0, 0.0), "confidence": 0.95},
        ],
    }

    graph = await build_semantic_graph(_DB(), "project-1", [drawing])

    room_nodes = [n for n in graph.nodes if n.node_type == "functional_space"]
    assert len(room_nodes) == 1
    node = room_nodes[0]
    sources = {e.source for e in graph.evidence if e.node_id == node.id}
    assert sources == {"title", "ocr_room_name"}
    # 两来源置信都被规则封顶在 0.8，低于 0.88 自动确认线——不因多来源就虚高确认
    assert node.confidence == pytest.approx(0.8)
    assert node.status == "candidate"


@pytest.mark.asyncio
async def test_build_semantic_graph_without_ocr_space_labels_key_unaffected():
    """未附加 ``ocr_space_labels`` 键的既有调用方行为不变。"""

    class _DB:
        async def fetch_all(self, *args, **kwargs):
            return []

    drawing = {"id": "d1", "drawing_no": "M-001", "title": "总说明"}

    graph = await build_semantic_graph(_DB(), "project-1", [drawing])

    assert graph.nodes == []
    assert graph.unassigned_drawings[0]["drawing_id"] == "d1"


@pytest.mark.unit
def test_consume_merge_into_semantics_input_does_not_mutate_drawing():
    """``consume.merge_into_semantics_input`` 遵循不可变约定：返回新 dict，不改原对象。"""
    drawing = {"id": "d1", "title": "总说明"}
    ocr_result = OcrResult(
        tokens=(TextToken("会议室", (0.0, 0.0, 2.0, 2.0), 0.9, "room_name"),),
        backend="mock",
    )

    patched = consume.merge_into_semantics_input(drawing, ocr_result)

    assert "ocr_space_labels" not in drawing  # 原对象未被修改
    assert patched["ocr_space_labels"][0]["text"] == "会议室"
    assert patched["id"] == "d1"  # 其余键原样透传


@pytest.mark.asyncio
async def test_build_semantic_graph_end_to_end_with_consume_merge_helper():
    """consume.merge_into_semantics_input → build_semantic_graph 全链路一次跑通。"""

    class _DB:
        async def fetch_all(self, *args, **kwargs):
            return []

    ocr_result = OcrResult(
        tokens=(TextToken("锅炉房", (0.0, 0.0, 2.0, 2.0), 0.92, "room_name"),),
        backend="mock",
    )
    drawing = consume.merge_into_semantics_input({"id": "d2", "title": "机电平面图"}, ocr_result)

    graph = await build_semantic_graph(_DB(), "project-1", [drawing])

    assert any(n.canonical_name == "锅炉房" for n in graph.nodes)
