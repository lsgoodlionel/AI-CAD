import pytest

from services.drawing_semantics import extract_semantic_candidates


@pytest.mark.unit
def test_structure_zone_is_sub_zone_not_building():
    drawing = {
        "title": "A、B、C区上人屋面总体布置图",
        "discipline": "structure",
        "folder_path": "结构竣工图",
    }

    candidates = extract_semantic_candidates(drawing)

    assert {(item.node_type, item.label) for item in candidates} >= {
        ("sub_zone", "A区"),
        ("sub_zone", "B区"),
        ("sub_zone", "C区"),
    }
    assert not any(item.node_type == "building_unit" for item in candidates)


@pytest.mark.unit
def test_functional_hall_and_enclosure_zone_keep_distinct_types():
    hall = extract_semantic_candidates({"title": "大歌剧厅舞台结构剖面图"})
    enclosure = extract_semantic_candidates(
        {
            "title": "2-2区车道联通道围护体平面图",
            "folder_path": "围护图纸",
        }
    )

    assert any(item.node_type == "functional_space" for item in hall)
    assert any(item.node_type == "construction_zone" for item in enclosure)


@pytest.mark.unit
def test_generic_building_markers_emit_building_unit_candidates():
    drawing = {
        "title": "3#楼A座结构平面图",
        "discipline": "architecture",
    }

    candidates = extract_semantic_candidates(drawing)

    assert {(item.node_type, item.label) for item in candidates} >= {
        ("building_unit", "3#楼"),
        ("building_unit", "A座"),
    }


@pytest.mark.unit
def test_directional_groups_are_candidates_not_confirmed_buildings():
    drawing = {
        "title": "东区校园道路及管线总平面图",
        "discipline": "general",
    }

    candidates = extract_semantic_candidates(drawing)
    east_zone = next(
        item
        for item in candidates
        if item.node_type == "sub_zone" and item.label == "东区"
    )

    assert east_zone.confidence < 0.9
    assert east_zone.context["match_reason"] == "directional_group"
    assert not any(item.node_type == "building_unit" for item in candidates)


@pytest.mark.unit
def test_industrial_and_infrastructure_terms_map_to_generic_types():
    factory = extract_semantic_candidates({"title": "一车间厂房剖面图"})
    garage = extract_semantic_candidates({"title": "地下车库通风平面图"})
    bridge = extract_semantic_candidates({"title": "1号桥桥梁第二联总体布置图"})
    tunnel = extract_semantic_candidates({"title": "隧道二工区开挖支护图"})

    assert any(
        item.node_type == "functional_space" and item.label == "一车间"
        for item in factory
    )
    assert any(
        item.node_type == "functional_space" and item.label == "地下车库"
        for item in garage
    )
    assert any(
        item.node_type == "construction_zone" and item.label == "1号桥"
        for item in bridge
    )
    assert any(
        item.node_type == "construction_zone" and item.label == "二工区"
        for item in tunnel
    )


@pytest.mark.unit
def test_only_allowed_node_types_are_emitted():
    candidates = extract_semantic_candidates(
        {
            "title": "东区1号桥二工区A座地下车库平面图",
            "folder_path": "施工图纸",
        }
    )

    assert {item.node_type for item in candidates} <= {
        "building_unit",
        "sub_zone",
        "functional_space",
        "construction_zone",
    }
