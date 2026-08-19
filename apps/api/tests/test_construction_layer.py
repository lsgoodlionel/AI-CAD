"""构造做法层识别 —— 会审 133 条检查项里「**做法**」出现 **47 条**，
是仅次于「说明」的第二高频要素。

它通往**真实构造层次**：一块 100 厚的板，实际是
「20 厚砂浆找平 + 3 厚防水 + 100 厚结构板 + 30 厚保温」——
建模时用的厚度、算量时用的材料量，都在这里。

**两个数据库实测形态**（每条构造层都是「厚度 + 材料」）：

    20厚DSM20预拌砂浆找平层        39 次
    3.0厚自粘改性沥青防水卷材      24 次
    30厚带铝箔岩棉板内保温         54 次
    ALC预制板斜墙100厚             66 次   ← 厚度在**后**

**必须防的误判**（同样含「厚」但不是构造层）：

    9.未注明楼梯平台板：板厚h=120，配筋双层双向钢筋10@200。   ← 设计说明
    8.除图中另有说明外，（梁高一板厚）>=450 应设腰筋…          ← 设计说明
"""
from __future__ import annotations

import pytest

from core.model3d.construction_layer import parse_construction_layer


@pytest.mark.unit
def test_thickness_before_material():
    """**主形态**:`20厚DSM20预拌砂浆找平层`。"""
    got = parse_construction_layer("20厚DSM20预拌砂浆找平层")
    assert got is not None
    assert got.thickness_mm == 20.0
    assert "砂浆" in got.material


@pytest.mark.unit
def test_decimal_thickness():
    """`3.0厚` / `1.5厚` —— 防水层常是小数。"""
    for text, mm in [("3.0厚自粘改性沥青防水卷材", 3.0),
                     ("1.5厚单组分聚氨酯防水涂膜防潮层", 1.5),
                     ("2厚高聚物改性沥青防水涂膜", 2.0)]:
        got = parse_construction_layer(text)
        assert got is not None and got.thickness_mm == mm, text


@pytest.mark.unit
def test_thickness_after_material():
    """**厚度在后**:`ALC预制板斜墙100厚`(实测 66 次)。"""
    got = parse_construction_layer("ALC预制板斜墙100厚")
    assert got is not None and got.thickness_mm == 100.0
    assert "ALC" in got.material


@pytest.mark.unit
def test_design_notes_are_rejected():
    """**实测误判源**:带编号的设计说明含「板厚」但不是构造层。"""
    for text in ("9.未注明楼梯平台板：板厚h=120，配筋双层双向钢筋10@200。",
                 "8.除图中另有说明外，（梁高一板厚）≥450应设腰筋，未注明梁腰筋设置见下表。",
                 "3.板厚未注明者均为100"):
        assert parse_construction_layer(text) is None, text


@pytest.mark.unit
def test_layer_role_is_classified():
    """识别构造层的**作用** —— 建模时保温层与结构层的处理完全不同。"""
    cases = [("20厚DSM20预拌砂浆找平层", "leveling"),
             ("3.0厚自粘改性沥青防水卷材", "waterproof"),
             ("30厚带铝箔岩棉板内保温", "insulation"),
             ("100厚C15混凝土垫层", "cushion"),
             ("120厚现浇钢筋混凝土板", "structural_slab")]
    for text, role in cases:
        got = parse_construction_layer(text)
        assert got is not None and got.role == role, f"{text} → {got}"


@pytest.mark.unit
def test_implausible_thickness_rejected():
    """**厚度要合理** —— 构造层不会是 0 或几米厚。"""
    for text in ("0厚砂浆", "5000厚保温层", "99999厚板"):
        assert parse_construction_layer(text) is None, text


@pytest.mark.unit
def test_plain_text():
    for text in ("", None, "一层平面图", "厚", "标高±0.000"):
        assert parse_construction_layer(text) is None, text


@pytest.mark.unit
def test_masonry_walls_are_structural():
    """**实测漏分**:`200厚蒸压加气混凝土砌块墙体` 落进 other。

    砌块/砌体/墙体是**承重或围护结构**,建模时要出实体,
    与找平层、保温层的处理完全不同。
    """
    for text in ("200厚蒸压加气混凝土砌块墙体", "240厚烧结页岩砖砌体",
                 "150厚加气块隔墙"):
        got = parse_construction_layer(text)
        assert got is not None and got.role == "structural_wall", f"{text} → {got}"


@pytest.mark.unit
def test_role_priority_is_stable():
    """**优先级要稳** —— 同时含多个关键词时按结构 > 防水 > 保温… 取。

    `100厚保温砌块` 既有「保温」又有「砌块」——它首先是墙体。
    """
    got = parse_construction_layer("100厚保温砌块墙")
    assert got is not None and got.role == "structural_wall"


# ── 结构层必须分墙与板（接建模前的必要细化）────────────────────

@pytest.mark.unit
def test_structural_splits_wall_and_slab():
    """**接建模前必须分开** —— 实测 `structural` 里混着墙和板:

        100厚ALC预制板斜墙        ← 墙（77 条 100mm 大多是它）
        200厚蒸压加气混凝土砌块墙体 ← 墙
        120厚现浇钢筋混凝土板      ← 板

    拿它做板厚会**把墙厚当板厚**。
    """
    walls = ("100厚ALC预制板斜墙", "200厚蒸压加气混凝土砌块墙体",
             "150厚加气块隔墙", "240厚烧结页岩砖砌体", "100厚轻质墙板")
    slabs = ("120厚现浇钢筋混凝土板", "100厚楼板", "150厚屋面板",
             "130厚叠合板", "180厚结构板")
    for text in walls:
        got = parse_construction_layer(text)
        assert got is not None and got.role == "structural_wall", f"{text} → {got}"
    for text in slabs:
        got = parse_construction_layer(text)
        assert got is not None and got.role == "structural_slab", f"{text} → {got}"


@pytest.mark.unit
def test_wall_wins_when_both_words_present():
    """`ALC预制板斜墙` 同时含「板」与「墙」—— **它是墙**。

    「预制板…墙」是墙的做法名（用板材砌的墙），不是楼板。
    以**末位构件词**为准：谁在最后，说的就是谁。
    """
    got = parse_construction_layer("100厚ALC预制板斜墙")
    assert got is not None and got.role == "structural_wall"
    got2 = parse_construction_layer("120厚墙上现浇板")
    assert got2 is not None and got2.role == "structural_slab"
