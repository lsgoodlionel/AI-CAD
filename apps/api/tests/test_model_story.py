import pytest

from services.model_story import MIN_STORY_SPACING_M, detect_building_unit, normalize_story_table


def _drawing(
    drawing_id: str,
    title: str,
    drawing_no: str,
    *,
    file_key: str = "",
    discipline: str = "architecture",
    ocr_text: str = "",
) -> dict:
    return {
        "id": drawing_id,
        "title": title,
        "drawing_no": drawing_no,
        "discipline": discipline,
        "file_key": file_key,
        "ocr_text": ocr_text,
    }


def test_detect_building_unit_uses_manual_override_and_dynamic_sources():
    manual = {
        "building_unit_key": "opera-west",
        "building_unit_display_name": "西区看台",
        "candidate_sources": [{"source": "manual", "value": "西区看台"}],
        "confidence": 1.0,
    }

    manual_match = detect_building_unit(
        _drawing("d1", "任意楼层平面图", "A-101", file_key="projects/p/2#楼/A-101.pdf"),
        manual,
    )
    assert manual_match.unit_key == "opera-west"
    assert manual_match.display_name == "西区看台"
    assert manual_match.source == "manual"

    detected = detect_building_unit(
        _drawing(
            "d2",
            "设备层节点图",
            "A-201",
            file_key="projects/p/总图/2#楼/设备层节点图.pdf",
            ocr_text="2#楼 设备层",
        )
    )
    assert detected.unit_key == "building_2"
    assert detected.display_name == "2#楼"
    assert {item["source"] for item in detected.candidate_sources} >= {"file_key", "ocr_text"}


def test_normalize_story_table_keeps_units_independent_and_queues_unclassified():
    drawings = [
        _drawing("south-1", "南区一层平面图", "A-S-101"),
        _drawing("south-2", "南区二层平面图", "A-S-201"),
        _drawing("north-1", "北区一层平面图", "A-N-101"),
        _drawing("detail-1", "楼梯节点详图", "A-D-001"),
    ]
    annotations = {
        "south-1": {"elevation_m": 0.0},
        "south-2": {"elevation_m": 1.2},
        "north-1": {"elevation_m": 100.0},
    }

    result = normalize_story_table(drawings, annotations)

    assert [story.story_key for story in result.stories_by_building["south"]] == ["F1", "F2"]
    assert result.stories_by_building["south"][0].elevation_m == pytest.approx(0.0)
    assert result.stories_by_building["south"][1].elevation_m == pytest.approx(4.5)
    assert result.stories_by_building["north"][0].elevation_m == pytest.approx(100.0)
    assert result.drawing_assignments["south-2"]["story_key"] == "F2"
    assert result.drawing_assignments["north-1"]["building_unit_key"] == "north"
    assert result.unclassified_drawings == [
        {
            "drawing_id": "detail-1",
            "drawing_no": "A-D-001",
            "title": "楼梯节点详图",
            "building_unit_key": "main",
            "reason": "story_unclassified",
        }
    ]
    issue = next(item for item in result.issues if item.issue_type == "story_spacing_too_small")
    assert issue.building_unit_key == "south"
    assert issue.story_key == "F2"
    assert issue.payload["detected_spacing_m"] < MIN_STORY_SPACING_M
