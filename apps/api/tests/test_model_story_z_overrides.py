"""标高表驱动层高替换测试（B-04）。

验证 normalize_story_table 的 z_overrides 优先级链（section > elevation > default），
provenance 贯穿：有实测→height_source=section/elevation、非估算；无→default 兜底且显式 estimated。
"""
import pytest

from services.model_story import (
    DEFAULT_STORY_HEIGHT_M,
    normalize_story_table,
)


def _drawing(drawing_id: str, title: str, drawing_no: str) -> dict:
    return {
        "id": drawing_id,
        "title": title,
        "drawing_no": drawing_no,
        "discipline": "architecture",
        "file_key": "",
        "ocr_text": "",
    }


def _two_story_drawings() -> list[dict]:
    return [
        _drawing("s1", "一层平面图", "A-101"),
        _drawing("s2", "二层平面图", "A-201"),
    ]


# ── 无 z_overrides：向后兼容 + 显式估算 ─────────────────────────

@pytest.mark.unit
def test_without_overrides_heights_are_default_and_estimated():
    result = normalize_story_table(_two_story_drawings())
    levels = result.stories_by_building["main"]

    assert levels[0].height_m == pytest.approx(DEFAULT_STORY_HEIGHT_M)
    assert levels[0].height_source == "default"
    assert levels[0].height_estimated is True
    assert "默认层高" in levels[0].height_note
    assert levels[0].height_confidence <= 0.55


@pytest.mark.unit
def test_without_overrides_backward_compatible_elevations():
    """不传 z_overrides 时标高/层高数值与既有默认行为一致（无回归）。"""
    result = normalize_story_table(_two_story_drawings())
    levels = result.stories_by_building["main"]
    assert [lvl.elevation_m for lvl in levels] == pytest.approx([0.0, 4.5])
    assert all(lvl.height_m == pytest.approx(DEFAULT_STORY_HEIGHT_M) for lvl in levels)


# ── 有 z_overrides：实测驱动 ────────────────────────────────────

@pytest.mark.unit
def test_section_override_drives_measured_height():
    overrides = {
        ("main", "F1"): {
            "height_m": 3.6,
            "elevation_bottom_m": 0.0,
            "source": "section",
            "confidence": 0.9,
        },
        ("main", "F2"): {
            "height_m": 3.0,
            "elevation_bottom_m": 3.6,
            "source": "section",
            "confidence": 0.88,
        },
    }
    result = normalize_story_table(_two_story_drawings(), z_overrides=overrides)
    levels = result.stories_by_building["main"]

    assert levels[0].height_m == pytest.approx(3.6)
    assert levels[0].height_source == "section"
    assert levels[0].height_estimated is False
    assert levels[0].height_note == ""
    assert levels[0].height_confidence == pytest.approx(0.9)
    # 实测标高覆盖 floor 底标高
    assert levels[1].elevation_m == pytest.approx(3.6)


@pytest.mark.unit
def test_partial_override_falls_back_to_default_for_missing_story():
    """仅 F1 有实测 → F2 回落默认并标估算（混合 provenance）。"""
    overrides = {
        ("main", "F1"): {
            "height_m": 3.6,
            "elevation_bottom_m": 0.0,
            "source": "section",
            "confidence": 0.9,
        },
    }
    result = normalize_story_table(_two_story_drawings(), z_overrides=overrides)
    levels = result.stories_by_building["main"]

    assert levels[0].height_estimated is False
    assert levels[0].height_source == "section"
    assert levels[1].height_estimated is True
    assert levels[1].height_source == "default"


@pytest.mark.unit
def test_elevation_source_override_marked_measured():
    overrides = {
        ("main", "F1"): {
            "height_m": 4.0,
            "elevation_bottom_m": 0.0,
            "source": "elevation",
            "confidence": 0.7,
        },
    }
    result = normalize_story_table(_two_story_drawings(), z_overrides=overrides)
    level = result.stories_by_building["main"][0]
    assert level.height_source == "elevation"
    assert level.height_estimated is False
