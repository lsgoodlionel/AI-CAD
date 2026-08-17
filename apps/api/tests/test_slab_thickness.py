"""结构楼板厚度提取单测(三个来源 + 排除建筑做法层)。纯函数。"""
from services.slab_thickness import extract_thickness_specs, pick_thickness


def test_extract_default_from_notes():
    """来源②图纸说明:「未注明板厚为120mm」是全图兜底默认值。"""
    specs = extract_thickness_specs(["1.本图未注明板面标高为-10.300m，未注明板厚为120mm，"])
    assert any(s["value_mm"] == 120 and s["kind"] == "default" for s in specs)


def test_extract_default_variant():
    specs = extract_thickness_specs(["2、未注明的楼板，坡道板板厚150，钢筋为10@200双层双向"])
    assert any(s["value_mm"] == 150 for s in specs)


def test_extract_plain_slab_annotation():
    """来源①平面图内标注。"""
    specs = extract_thickness_specs(["底板（板厚1700）", "板厚150，"])
    values = {s["value_mm"] for s in specs}
    assert 1700 in values and 150 in values


def test_extract_raft_thickness():
    """来源③剖面/基础:筏板厚度 H=1000mm。"""
    specs = extract_thickness_specs(["筏板厚度H=1000mm", "h=800mm"])
    kinds = {(s["value_mm"], s["kind"]) for s in specs}
    assert (1000, "raft") in kinds
    assert any(v == 800 for v, _ in kinds)


def test_material_layers_excluded():
    """**关键**:建筑做法层不是结构板厚(档案里这类占绝大多数)。"""
    specs = extract_thickness_specs([
        "250厚级配碎石垫层", "80厚花岗岩石材", "20厚DSM20预拌砂浆找平层",
        "150厚保温岩棉", "50厚预制混凝土盖板",
    ])
    assert specs == []


def test_out_of_range_filtered():
    """超合理区间(如栓钉 19h=80 已在下限;9999 超上限)。"""
    specs = extract_thickness_specs(["板厚9999", "板厚10"])
    assert specs == []


def test_pick_prefers_default_over_scattered():
    """说明里的默认值覆盖全图,优先于零散标注。"""
    specs = [{"value_mm": 120, "kind": "default", "raw": "未注明板厚120"},
             {"value_mm": 200, "kind": "slab", "raw": "板厚200"}]
    got = pick_thickness(specs)
    assert got["value_m"] == 0.12 and got["kind"] == "default"


def test_pick_majority_within_kind():
    """同类取众数(多处一致更可信)。"""
    specs = [{"value_mm": 150, "kind": "slab", "raw": "板厚150"},
             {"value_mm": 150, "kind": "slab", "raw": "板厚150"},
             {"value_mm": 300, "kind": "slab", "raw": "板厚300"}]
    got = pick_thickness(specs)
    assert got["value_m"] == 0.15 and got["support"] == 2


def test_pick_raft_for_foundation():
    specs = [{"value_mm": 120, "kind": "default", "raw": "x"},
             {"value_mm": 1700, "kind": "raft", "raw": "筏板厚度H=1700"}]
    got = pick_thickness(specs, is_raft=True)
    assert got["value_m"] == 1.7 and got["kind"] == "raft"


def test_pick_empty():
    assert pick_thickness([]) is None


def test_apply_scene_slab_thickness():
    """写入 scene 并标注来源(便于追溯/人审);无提取值的板保持原值。"""
    from services.slab_thickness import apply_scene_slab_thickness
    floors = [{"elements": {"slabs": [
        {"src": "d1", "thickness": 0.12},
        {"src": "d2", "thickness": 0.12},
    ]}}]
    stat = apply_scene_slab_thickness(floors, {
        "d1": {"value_m": 0.15, "kind": "default", "raw": "未注明板厚150"}})
    assert stat == {"updated": 1, "total": 2}
    slabs = floors[0]["elements"]["slabs"]
    assert slabs[0]["thickness"] == 0.15
    assert slabs[0]["thickness_source"] == "default"
    assert slabs[1]["thickness"] == 0.12          # 无提取值 → 保持兜底
    assert "thickness_source" not in slabs[1]
