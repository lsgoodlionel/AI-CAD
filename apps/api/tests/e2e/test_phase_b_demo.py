"""Phase B 里程碑 E2E Demo（B-24）：整套图 → 真实几何 → 拓扑 → 算量 → 创效。

真实脱敏整套图样本为前置阻塞项；本 Demo 用确定性合成几何（对齐详设 §7.1）
在进程内串联全链路，逐条勾对 Phase B 验收总标准 1–5，并对照「缺剖面」降级。

链路：B-01 判图种 → B-08 轴网 → B-02 剖面标高 → B-06 立面洞口 → B-09 配准
     → B-10 统一入口 → B-15 拓扑 → B-16/17/18 算量 → B-19 汇总 → B-20 创效草稿。
"""
import pytest

from core.ai_review.cross_view_z import recover_z_from_geometries
from core.model3d.types import DrawingGeometry
from services.model_lod import ModelScopeEvidence, evaluate_lod_capability
from services.model_qto import compute_quantities, compute_rebar_quantities
from services.model_qto_summary import build_scene_quantities
from services.model_topology import build_topology_graph


# ── 合成整套图 ─────────────────────────────────────────────────

def _plan():
    lines = [(x, 40, x, 760) for x in (100, 300, 500)]
    lines += [(40, y, 560, y) for y in (100, 400, 700)]
    texts = [(100, 20, "1"), (300, 20, "2"), (500, 20, "3"),
             (20, 100, "A"), (20, 400, "B"), (20, 700, "C")]
    return {"id": "plan1", "title": "一层平面图", "drawing_no": "A-101"}, \
        DrawingGeometry(page_w=600, page_h=800, lines=lines, texts=texts)


def _section():
    lines = [(40, 760, 560, 760), (40, 560, 560, 560), (40, 360, 560, 360)]
    texts = [(575, 760, "±0.000"), (575, 560, "+3.000"), (575, 360, "+6.000"),
             (300, 450, "KL1 300×600"), (300, 250, "板厚120")]
    return {"id": "sec1", "title": "1-1剖面图", "drawing_no": "A-501"}, \
        DrawingGeometry(page_w=600, page_h=800, lines=lines, texts=texts)


def _elevation():
    lines = [(40, 760, 560, 760), (40, 560, 560, 560), (40, 360, 560, 360)]
    lines += [(x, 40, x, 780) for x in (100, 300, 500)]
    texts = [(575, 760, "±0.000"), (575, 560, "+3.000"), (575, 360, "+6.000"),
             (100, 20, "1"), (300, 20, "2"), (500, 20, "3")]
    rects = [(150, 500, 100, 100, False)]
    return {"id": "elev1", "title": "南立面图", "drawing_no": "A-601"}, \
        DrawingGeometry(page_w=600, page_h=800, lines=lines, texts=texts, rects=rects)


def _connected_elements():
    def _col(cx, cy, half=0.25):
        return {"outline": [[cx - half, cy - half], [cx + half, cy - half],
                            [cx + half, cy + half], [cx - half, cy + half]]}
    return {
        "columns": [_col(0, 0), _col(6, 0), _col(6, 6), _col(0, 6)],
        "beams": [
            {"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6},
            {"path": [[6, 0], [6, 6]], "width": 0.3, "depth": 0.6},
            {"path": [[6, 6], [0, 6]], "width": 0.3, "depth": 0.6},
            {"path": [[0, 6], [0, 0]], "width": 0.3, "depth": 0.6},
        ],
        "slabs": [{"outline": [[0, 0], [6, 0], [6, 6], [0, 6]], "thickness": 0.12}],
        "walls": [],
    }


_REBAR = [
    {"diameter": 20, "steel_grade": "HRB400", "required_length": 6000, "count": 40},
    {"diameter": 8, "steel_grade": "HPB300", "required_length": 2000, "count": 60},
]


def _lod_scope(*, cross_view_match: bool, topo_evidence: dict) -> ModelScopeEvidence:
    """由 Phase B 证据装配 scope：平面/层序/比例/坐标齐备，配准与拓扑证据可变。"""
    return ModelScopeEvidence(
        scope_key="scene",
        has_plan_boundary=True,
        has_story_order=True,
        has_scale=True,
        has_coordinates=True,
        has_registered_grid=True,
        has_dimensions=True,
        has_cross_view_match=cross_view_match,
        has_stable_component_boundaries=topo_evidence["stable_component_boundaries"],
        geometry_consistent=topo_evidence["geometry_consistent"],
    )


# ── 验收标准 1：含剖面套图 → 真实几何 + LOD300 ────────────────

@pytest.mark.e2e
def test_standard_1_full_set_real_geometry_and_lod300():
    recovery = recover_z_from_geometries([_plan(), _section(), _elevation()])

    # 真实层高（非默认 4.5）：F1/F2 底标高 0/3/6 → 层高 3.0
    assert [lvl["elevation_m"] for lvl in recovery.levels] == [0.0, 3.0, 6.0]
    assert recovery.levels[0]["source"] == "section"
    # 真实梁高/板厚（非硬编码 0.6/0.12）
    assert recovery.component_sections["beam"].h_m == pytest.approx(0.6)
    assert recovery.component_sections["beam"].estimated is False
    assert recovery.component_sections["slab"].thickness_m == pytest.approx(0.12)
    # 三视图配准一致 → cross_view_match 强证据
    assert recovery.matched is True

    topo = build_topology_graph(**_connected_elements(), openings=[]).lod_evidence()
    scope = _lod_scope(cross_view_match=recovery.matched, topo_evidence=topo)
    assert evaluate_lod_capability(scope).level == 300


# ── 验收标准 2：缺剖面 → 显式估算降级，gate 不虚高 ────────────

@pytest.mark.e2e
def test_standard_2_missing_section_degrades_explicitly():
    recovery = recover_z_from_geometries([_plan()])   # 仅平面

    assert recovery.levels == ()                       # 无实测标高
    assert recovery.matched is False                   # 配准强证据缺失
    assert recovery.component_sections["beam"].estimated is True   # 截面回落默认+估算

    topo = build_topology_graph(columns=[], beams=[], slabs=[], walls=[], openings=[]).lod_evidence()
    scope = _lod_scope(cross_view_match=recovery.matched, topo_evidence=topo)
    # cross_view_match 与拓扑证据均缺 → LOD 诚实停在 200，不虚高
    assert evaluate_lod_capability(scope).level < 300


# ── 验收标准 3：IFC 算量出混凝土 + 钢筋 + 模板 ────────────────

@pytest.mark.e2e
def test_standard_3_qto_concrete_rebar_formwork():
    quantities = compute_quantities(_connected_elements(), story_height_m=3.0)
    beams = [q for q in quantities if q.element_type == "beam"]
    slabs = [q for q in quantities if q.element_type == "slab"]

    # 净体积经拓扑扣减 < 毛体积（梁嵌柱、板压梁）
    assert beams[0].net_volume_m3 < beams[0].gross_volume_m3
    assert slabs[0].net_volume_m3 < slabs[0].gross_volume_m3
    # 模板接触面 > 0
    assert beams[0].formwork_contact_m2 > 0
    # 钢筋量（复用 rebar_calculator）
    rebar = compute_rebar_quantities(_REBAR)
    assert rebar["rebar_missing"] is False
    assert rebar["total_steel_kg"] > 0


# ── 验收标准 4：QTO → 创效测算草稿（net_saving > 0）──────────

@pytest.mark.e2e
def test_standard_4_qto_to_incentive_draft_positive_saving():
    scene = {
        "floors": [{"key": "F1", "label": "1层", "building_units": ["main"],
                    "elements": _connected_elements()}],
        "quality": {"story_tables": {"main": [{"story_key": "F1", "height_m": 3.0}]}},
    }
    data = build_scene_quantities(scene, rebar_inputs=_REBAR)
    assert data["project"]["concrete"]["net_m3"] > 0
    assert data["project"]["rebar"]["missing"] is False

    # 钢筋下料优化节约（创效草稿 raw_saving_est 来源）> 0
    rebar = compute_rebar_quantities(_REBAR)
    assert rebar["summary"]["saving_yuan"] > 0


# ── 验收标准 5：全链路可离线复现（无外部服务）────────────────

@pytest.mark.e2e
def test_standard_5_full_chain_offline_reproducible():
    """含剖面 vs 缺剖面两条链路均离线确定性跑通，结果对照鲜明。"""
    full = recover_z_from_geometries([_plan(), _section(), _elevation()])
    degraded = recover_z_from_geometries([_plan()])

    assert full.matched is True and degraded.matched is False
    assert bool(full.levels) and not degraded.levels
    assert full.component_sections["beam"].estimated is False
    assert degraded.component_sections["beam"].estimated is True
