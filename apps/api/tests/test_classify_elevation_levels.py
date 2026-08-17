"""标高与楼层名的分类 —— 依据 GB/T 50001 §11.8 与 §9。

**实测来源**:上海大歌剧院 A-30-07A(7-7/8-8/9-9/10-10 剖面图)。
图上右侧标高链白纸黑字写着 `21.890 大歌剧厅顶板` / `16.200 4F` /
`10.800 3F` / `5.400 2F` / `±0.000 1F`,层高尺寸链 `5690/5400/5400`
与标高差完全吻合。而系统把 `4F`/`3F`/`2F`/`1F` 全部归进 `other`,
楼层名整条丢失 —— 这正是建模最需要的楼层标高表。

国标依据:
* §11.8.4 标高数字以**米**为单位,注写到**小数点后第三位**
* §11.8.5 零点写 `±0.000`,正数不注 `+`,负数注 `−`
* §9 除标高及总平面以米为单位外,其余尺寸**必须以毫米为单位**
  —— 于是「毫米整数」与「米制三位小数」是可判的两类
"""
from __future__ import annotations

import pytest

from core.model3d.ocr.classify import classify_text


@pytest.mark.unit
@pytest.mark.parametrize("text", ["1F", "2F", "4F", "12F", "B1", "B2", "RF", "F1"])
def test_short_floor_marks_are_level_names(text):
    """`1F`/`B1`/`RF` 是工程最常用的楼层标记,此前全部落进 other。"""
    assert classify_text(text)[0] == "level_name"


@pytest.mark.unit
@pytest.mark.parametrize("text", ["四层", "地下二层", "首层", "屋面", "夹层"])
def test_chinese_level_names_still_recognised(text):
    """旧行为要保住。"""
    assert classify_text(text)[0] == "level_name"


@pytest.mark.unit
@pytest.mark.parametrize("text,value", [
    ("±0.000", 0.0), ("16.200", 16.2), ("5.400", 5.4),
    ("-4.200", -4.2), ("+21.890", 21.89), ("21.890", 21.89),
])
def test_metre_three_decimal_is_elevation(text, value):
    """§11.8.4:标高以米为单位、小数点后三位。"""
    kind, got = classify_text(text)
    assert kind == "elevation"
    assert got == pytest.approx(value)


@pytest.mark.unit
@pytest.mark.parametrize("text", ["3650", "5400", "5690", "100", "21890"])
def test_millimetre_integer_is_dimension_not_elevation(text):
    """§9:除标高外尺寸必须以毫米为单位 —— 毫米整数不是标高。

    实测 7-7 剖面的 `3650`(吊顶净高)、`5400`(层高)、`100`(板厚)。
    """
    assert classify_text(text)[0] == "dimension"


@pytest.mark.unit
def test_single_character_is_not_a_room_name():
    """图框会签栏被逐字拆开 —— `校/合/作/设/计/单/位/审/定/期` 不是房间。

    实测 A-20-01A 有 127 条这种单字 room_name,全是噪声。
    """
    for ch in "校合作设计单位审定期总负责核专业对建项目名称图工":
        assert classify_text(ch)[0] != "room_name", ch


@pytest.mark.unit
def test_two_character_room_names_still_work():
    """真房间名不能被误伤。"""
    for name in ("男卫", "女卫", "前厅", "机房"):
        assert classify_text(name)[0] == "room_name", name


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "大歌剧厅4F", "大歌剧厅3F", "大歌剧厅屋顶层", "小歌剧厅B1", "6F（设备层）",
])
def test_prefixed_floor_marks_are_level_names(text):
    """带部位前缀的楼层名 —— 实测 A-20-02A 南立面图标高链用的正是这种写法。

    `大歌剧厅3F 10.300` / `大歌剧厅4F 16.100` / `大歌剧厅屋顶层 45.500`,
    此前因长度超阈值被判为 note/room_name,楼层名整条丢失。
    """
    assert classify_text(text)[0] == "level_name"


@pytest.mark.unit
@pytest.mark.parametrize("text", ["前厅", "男卫", "观众厅", "候场区"])
def test_room_names_are_not_swallowed_by_the_floor_rule(text):
    """房间名不能被楼层规则误伤。"""
    assert classify_text(text)[0] == "room_name", text


# ── 「标高」字样 ≠ 标高标注 ────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "33.330标高钢桁架轮廓",
    "窗顶标高43.100，窗底标高42.350",
    "水箱架空安装，架空乘项标高42.20m",
    "标高43.830，窗底标高42.350",
    "屋顶观景平台标高按建筑做法确定",
])
def test_prose_containing_the_word_elevation_is_a_note(text):
    """**含「标高」二字的说明文字不是标高标注**。

    实测:全项目 38810 条 elevation 里 **18127 条(47%)** 是这种长串。
    它们被当成标高后:
    * 取出的数值可能是别的东西(`窗底标高：34250` 里 34250 是**毫米**);
    * 一句话里有两个标高值时只取第一个;
    * 严重污染楼层标高配对与统计。

    国标 §11.8.4 规定标高是**米制三位小数的数字**,标注在标高符号旁,
    不是一段话。
    """
    assert classify_text(text)[0] != "elevation"


@pytest.mark.unit
@pytest.mark.parametrize("text,value", [
    ("标高10.800", 10.8),
    ("标高 16.200", 16.2),
    ("建筑标高±0.000", 0.0),
    ("结构标高-4.200", -4.2),
])
def test_short_elevation_annotations_still_count(text, value):
    """「标高 X.XXX」这种**短标注**仍是标高 —— 不能误伤。"""
    kind, got = classify_text(text)
    assert kind == "elevation"
    assert got == pytest.approx(value)


@pytest.mark.unit
def test_millimetre_after_the_word_elevation_is_not_metres():
    """`窗底标高：34250` 里 34250 是**毫米**(§9),不是 34.250 米。"""
    assert classify_text("窗底标高：34250")[0] != "elevation"
