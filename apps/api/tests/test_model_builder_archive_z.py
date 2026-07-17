"""
E2-consume 建模从档案读标高单测(Phase E2-consume)

_section_levels_from_archive:档案标高项 → SectionLevels(纯函数,替代自跑 OCR)。
"""
from services.model_builder import _section_levels_from_archive


def _elev(drawing_id, value, conf=0.9, source="auto"):
    return {
        "drawing_id": drawing_id, "category": "elevation",
        "content": f"{value:+.3f}", "value_json": {"elevation_m": value},
        "confidence": conf, "source_kind": source,
    }


def test_archive_elevations_become_section_levels():
    items = [_elev("d1", -2.35), _elev("d1", 3.6), _elev("d1", 7.2)]
    levels = _section_levels_from_archive(items)

    assert len(levels.marks) == 3
    elevs = sorted(m.elevation_m for m in levels.marks)
    assert elevs == [-2.35, 3.6, 7.2]
    assert levels.fit.get("archive") is True


def test_verified_elevation_confidence_capped_high():
    items = [_elev("d1", -2.40, conf=None, source="verified")]
    levels = _section_levels_from_archive(items)

    assert len(levels.marks) == 1
    # verified 人审值 → 满置信
    assert levels.marks[0].confidence >= 0.99


def test_empty_archive_yields_no_marks():
    assert _section_levels_from_archive([]).marks == ()


def test_deduplicates_same_elevation():
    items = [_elev("d1", 3.6), _elev("d1", 3.60)]
    levels = _section_levels_from_archive(items)
    assert len(levels.marks) == 1
