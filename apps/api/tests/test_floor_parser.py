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


# ── 数值合理性钳制（上海大歌剧院实测：图内文本 B80 被误判为地下八十层）──

@pytest.mark.unit
def test_basement_number_out_of_range_rejected():
    """地下层超过合理范围（>9）→ 视为非楼层文本（轴号/桩号等伪匹配）"""
    from services.floor_parser import parse_floor
    assert parse_floor("B80 钢筋编号") is None
    assert parse_floor("地下八十层") is None


@pytest.mark.unit
def test_above_ground_number_out_of_range_rejected():
    """地上层超过 120 层 → 拒绝（编号伪匹配）"""
    from services.floor_parser import parse_floor
    assert parse_floor("300层") is None


@pytest.mark.unit
def test_reasonable_floors_still_parse():
    from services.floor_parser import parse_floor
    assert parse_floor("B2 平面")[0] == "B2"
    assert parse_floor("地下二层")[0] == "B2"
    assert parse_floor("15层平面图")[0] == "F15"


# ── 可信楼层集约束（全量实测：issue levels 文本 B9/61F 产生伪楼层）──

@pytest.mark.unit
def test_issue_levels_floor_rejected_when_not_trusted():
    """issue levels 推出的楼层不在可信集（title 解析出的楼层）→ 归 UNZONED"""
    from services.floor_parser import floor_of_drawing

    drawing = {"title": "栈桥区域立柱详图", "drawing_no": "WH-61"}
    result = floor_of_drawing(drawing, ["B9"], trusted_keys={"B2", "B1", "F1"})
    assert result[0] == "UNZONED"


@pytest.mark.unit
def test_issue_levels_floor_kept_when_trusted():
    from services.floor_parser import floor_of_drawing

    drawing = {"title": "栈桥区域立柱详图", "drawing_no": "WH-61"}
    result = floor_of_drawing(drawing, ["B2"], trusted_keys={"B2", "B1"})
    assert result[0] == "B2"


@pytest.mark.unit
def test_title_floor_not_affected_by_trusted_keys():
    """title 直接解析的楼层不受可信集约束（title 本身就是可信来源）"""
    from services.floor_parser import floor_of_drawing

    drawing = {"title": "地下二层结构平面图", "drawing_no": "S-1"}
    assert floor_of_drawing(drawing, [], trusted_keys={"F1"})[0] == "B2"
