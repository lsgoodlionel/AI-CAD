"""楼层解析测试（parse_floor / floor_of_drawing 各分支）"""
import pytest

from services.floor_parser import UNZONED_FLOOR, floor_of_drawing, parse_floor


# ── parse_floor：地下层 ───────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("B2", ("B2", "地下二层", -2)),
    ("JS-B2-01", ("B2", "地下二层", -2)),
    ("地下二层人防平面图", ("B2", "地下二层", -2)),
    ("地下1层平面图", ("B1", "地下一层", -1)),
    ("负三层结构图", ("B3", "地下三层", -3)),
])
def test_parse_floor_basement(text, expected):
    assert parse_floor(text) == expected


# ── parse_floor：地上层 ───────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("3F", ("F3", "3层", 3)),
    ("JG-3F-05", ("F3", "3层", 3)),
    ("三层结构平面图", ("F3", "3层", 3)),
    ("3层给排水平面图", ("F3", "3层", 3)),
    ("十二层建筑平面图", ("F12", "12层", 12)),
    ("二十层平面图", ("F20", "20层", 20)),
])
def test_parse_floor_above_ground(text, expected):
    assert parse_floor(text) == expected


# ── parse_floor：屋面 / 基础 / 无法识别 ──────────────────────

def test_parse_floor_roof():
    assert parse_floor("屋面排水平面图") == ("RF", "屋面", 99)
    assert parse_floor("屋顶花园布置图") == ("RF", "屋面", 99)


def test_parse_floor_foundation():
    assert parse_floor("基础平面布置图") == ("FD", "基础层", -98)
    assert parse_floor("承台配筋图") == ("FD", "基础层", -98)


@pytest.mark.parametrize("text", ["", "总平面图", "电气系统图", None])
def test_parse_floor_returns_none_when_unmatched(text):
    assert parse_floor(text or "") is None


def test_parse_floor_basement_takes_priority_over_floor_suffix():
    # "地下二层" 同时含 "二层"，必须命中地下分支
    assert parse_floor("地下二层") == ("B2", "地下二层", -2)


# ── floor_of_drawing ─────────────────────────────────────────

def test_floor_of_drawing_prefers_title():
    drawing = {"title": "三层结构平面图", "drawing_no": "JG-B1-01"}
    assert floor_of_drawing(drawing, []) == ("F3", "3层", 3)


def test_floor_of_drawing_falls_back_to_drawing_no():
    drawing = {"title": "结构平面图", "drawing_no": "JG-B1-01"}
    assert floor_of_drawing(drawing, []) == ("B1", "地下一层", -1)


def test_floor_of_drawing_uses_issue_levels_mode():
    drawing = {"title": "结构平面图", "drawing_no": "JG-01"}
    levels = ["3F", "B2", "3F"]
    assert floor_of_drawing(drawing, levels) == ("F3", "3层", 3)


def test_floor_of_drawing_unzoned_when_nothing_matches():
    drawing = {"title": "总说明", "drawing_no": "JG-00"}
    assert floor_of_drawing(drawing, ["无定位"]) == UNZONED_FLOOR


def test_floor_of_drawing_handles_missing_fields():
    assert floor_of_drawing({}, []) == UNZONED_FLOOR
