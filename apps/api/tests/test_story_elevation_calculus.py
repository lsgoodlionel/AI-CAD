"""层高 ↔ 标高计算关系单测(工程约束)。纯函数。"""
from services.story_elevation_calculus import (
    cross_validate,
    elevations_from_heights,
    heights_from_elevations,
    unreasonable_heights,
)


def _levels():
    return [
        {"story_key": "B1", "order": -1, "elevation_m": -4.2},
        {"story_key": "F1", "order": 1, "elevation_m": 0.0},
        {"story_key": "F2", "order": 2, "elevation_m": 4.5},
        {"story_key": "F3", "order": 3, "elevation_m": 9.0},
    ]


def test_heights_from_elevations_is_difference():
    """上下层楼板标高之差即该层层高(工程定义)。"""
    hs = {h["story_key"]: h["height_m"] for h in heights_from_elevations(_levels())}
    assert hs["B1"] == 4.2      # -4.2 → 0.0
    assert hs["F1"] == 4.5
    assert hs["F2"] == 4.5
    assert hs["F3"] is None     # 顶层无上层


def test_heights_flag_unreasonable():
    levels = [{"story_key": "A", "order": 1, "elevation_m": 0.0},
              {"story_key": "B", "order": 2, "elevation_m": 30.0}]   # 30m 层高
    bad = unreasonable_heights(levels)
    assert len(bad) == 1 and bad[0]["story_key"] == "A"


def test_elevations_from_heights_accumulates_both_ways():
    """基准标高 + 层高 → 向上累加、向下回减。"""
    heights = [
        {"story_key": "B1", "order": -1, "height_m": 4.2},
        {"story_key": "F1", "order": 1, "height_m": 4.5},
        {"story_key": "F2", "order": 2, "height_m": 4.5},
    ]
    out = {o["story_key"]: o["elevation_m"]
           for o in elevations_from_heights("F1", 0.0, heights)}
    assert out["F1"] == 0.0
    assert out["F2"] == 4.5      # 向上
    assert out["B1"] == -4.2     # 向下回减


def test_elevations_from_heights_unknown_base():
    assert elevations_from_heights("ZZ", 0.0, [{"story_key": "F1", "order": 1}]) == []


def test_cross_validate_consistent():
    """标高差分 与 平面标注层高 一致 → 无冲突。"""
    heights = [{"story_key": "B1", "height_m": 4.2},
               {"story_key": "F1", "height_m": 4.5},
               {"story_key": "F2", "height_m": 4.5}]
    r = cross_validate(_levels(), heights)
    assert r["consistent"] is True
    assert r["checked"] == 3


def test_cross_validate_locates_conflict():
    """不自洽处即数据错误所在——精确定位到楼层。"""
    heights = [{"story_key": "F1", "height_m": 6.0}]     # 实际标高差 4.5
    r = cross_validate(_levels(), heights)
    assert r["consistent"] is False
    assert r["conflicts"][0]["story_key"] == "F1"
    assert r["conflicts"][0]["from_elevations"] == 4.5
    assert r["conflicts"][0]["from_heights"] == 6.0
    assert abs(r["conflicts"][0]["diff_m"] - 1.5) < 1e-6


def test_cross_validate_tolerates_ocr_jitter():
    """容差内(OCR/换算抖动)不算冲突。"""
    r = cross_validate(_levels(), [{"story_key": "F1", "height_m": 4.52}])
    assert r["consistent"] is True


def test_empty_inputs():
    assert heights_from_elevations([]) == []
    assert cross_validate([], [])["checked"] == 0
