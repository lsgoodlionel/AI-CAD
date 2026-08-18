"""标高的跨图共识：**孤证不立，多证可立**（标高覆盖 10 层 → 预期 3 倍）。

**实测漏斗**（上海大歌剧院）：

| 级 | 图数 |
|---|---:|
| 同图有标高+楼层名 | 1578 |
| **链式配对（现行唯一通道）** | **65（4.1%）** |
| 自由配对 | 184 |

链式判据是为准确性设的（防图例区数字乱配），不能放开单图判据；
但**跨图共识**同样是强证据：自由对若有 ≥2 张图给同一（单体,楼层,标高），
乱配不可能在多张图上撞出同一个值。实测多图背书覆盖 42 个组合。

**假冲突的真相**：实测 9 个「冲突」大多是单体混淆 —— 楼层名自己带着单体
（「大歌剧厅3F」「小歌剧厅2F」），而外部单体分类器返回 None，
不同单体的同名层撞在一起。所以先从楼层名抽单体，再聚合。
"""
from __future__ import annotations

import pytest

from services.level_elevation_consensus import (
    MIN_WITNESS_DRAWINGS, consensus_overrides, split_unit_from_level,
)


# ── 楼层名里的单体 ───────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("name,unit,level", [
    ("大歌剧厅3F", "大歌剧厅", "3F"),
    ("小歌剧厅2F", "小歌剧厅", "2F"),
    ("中歌剧厅4F", "中歌剧厅", "4F"),
    ("3F", None, "3F"),
    ("5F（设备层）", None, "5F（设备层）"),
])
def test_unit_is_split_from_the_level_name(name, unit, level):
    """楼层名自带单体时，它比外部分类器可靠（后者实测返回 None）。"""
    assert split_unit_from_level(name) == (unit, level)


# ── 多证可立 ─────────────────────────────────────────────────────

def _p(did, level, elev, unit=None):
    return {"drawing_id": did, "level_name": level,
            "elevation_m": elev, "building_unit_key": unit}


@pytest.mark.unit
def test_two_witnesses_make_an_override():
    """**核心用例**：两张图给同一（楼层,标高）⇒ 采纳。"""
    got = consensus_overrides([_p("a", "3F", 10.8), _p("b", "3F", 10.8)])
    assert len(got) == 1
    assert got[0]["elevation_m"] == pytest.approx(10.8)
    assert got[0]["witnesses"] == 2


@pytest.mark.unit
def test_a_single_witness_is_not_enough():
    """**孤证不立** —— 单图的自由配对可能是图例区数字乱配。"""
    assert consensus_overrides([_p("a", "3F", 10.8)]) == []


@pytest.mark.unit
def test_same_drawing_twice_is_still_one_witness():
    """同一张图重复出现不算两票 —— 票数按**图**数,不按条目数。"""
    assert consensus_overrides([_p("a", "3F", 10.8), _p("a", "3F", 10.8)]) == []


@pytest.mark.unit
def test_conflicting_values_produce_no_override_but_a_conflict():
    """**真冲突只报不选**（用户口径：矛盾时出矛盾点交人判断）。"""
    pairs = [_p("a", "4F", 16.2), _p("b", "4F", 16.2),
             _p("c", "4F", 12.6), _p("d", "4F", 12.6)]
    overrides = consensus_overrides(pairs)
    assert overrides == []
    from services.level_elevation_consensus import consensus_conflicts

    conflicts = consensus_conflicts(pairs)
    assert len(conflicts) == 1
    assert sorted(v for v, _n in conflicts[0]["values"]) == [12.6, 16.2]


@pytest.mark.unit
def test_units_separate_identically_named_levels():
    """**单体分开后假冲突消失**：大歌剧厅 3F=10.3 与 north 3F=9.35 不冲突。"""
    pairs = [_p("a", "大歌剧厅3F", 10.3), _p("b", "大歌剧厅3F", 10.3),
             _p("c", "3F", 9.35, unit="north"), _p("d", "3F", 9.35, unit="north")]
    got = consensus_overrides(pairs)
    assert len(got) == 2
    units = {o["building_unit_key"] for o in got}
    assert units == {"大歌剧厅", "north"}


@pytest.mark.unit
def test_level_name_unit_beats_the_external_classifier():
    """楼层名自带的单体**覆盖**外部分类器给的 —— 前者写在图上，后者靠猜。"""
    got = consensus_overrides([_p("a", "小歌剧厅2F", 4.8, unit="south"),
                               _p("b", "小歌剧厅2F", 4.8, unit=None)])
    assert len(got) == 1
    assert got[0]["building_unit_key"] == "小歌剧厅"


@pytest.mark.unit
def test_witness_threshold_is_at_least_two():
    assert MIN_WITNESS_DRAWINGS >= 2


@pytest.mark.unit
def test_empty_input_is_safe():
    assert consensus_overrides([]) == []
    assert consensus_overrides(None) == []


# ── 共识对进 build_z_overrides 不能再被「孤证不立」杀一遍 ─────────

@pytest.mark.unit
def test_consensus_expands_to_one_pair_per_witness():
    """**实测断点**：共识补 19 层、最终产出仍 10 层 ——
    `build_z_overrides` 的 `MIN_SAMPLES=2` 把每条共识对当 1 个样本杀掉，
    「孤证不立」被重复计了两次。

    修法是**如实展开**：共识对有 N 张见证图，就是 N 个独立样本 ——
    每张图各出一条 pair，不是权重 hack。
    """
    from services.level_elevation_consensus import consensus_to_pairs

    items = [{"building_unit_key": None, "level_name": "3F",
              "elevation_m": 10.8, "witnesses": 3,
              "drawing_ids": ["a", "b", "c"],
              "source": "cross_drawing_consensus"}]
    pairs = consensus_to_pairs(items)
    assert len(pairs) == 3
    assert all(p["elevation_m"] == pytest.approx(10.8) for p in pairs)
    assert {p["level_name"] for p in pairs} == {"3F"}


@pytest.mark.unit
def test_expansion_of_nothing_is_safe():
    from services.level_elevation_consensus import consensus_to_pairs

    assert consensus_to_pairs([]) == []
    assert consensus_to_pairs(None) == []


# ── 厅名单体 → 楼层表单体:从见证图学映射,不硬编码词表 ─────────────

@pytest.mark.unit
def test_fallback_unit_is_learned_from_witnesses():
    """**实测死因**:7 条共识项的单体是「大/中/小歌剧厅」,而楼层表只有
    main/north/south ⇒ 全被「该单体没有这一层」拒掉。

    映射不能硬编码词表(用户约束:名称体系不得硬编码)——
    但**见证图自己带着答案**:「小歌剧厅2F」的见证图,外部分类器
    判它们属哪个区,一致票就是厅→区的映射,从数据学出来的。
    """
    pairs = [_p("a", "小歌剧厅2F", 4.8, unit="north"),
             _p("b", "小歌剧厅2F", 4.8, unit="north"),
             _p("c", "小歌剧厅2F", 4.8, unit=None)]
    got = consensus_overrides(pairs)
    assert len(got) == 1
    assert got[0]["building_unit_key"] == "小歌剧厅"
    assert got[0]["fallback_unit"] == "north"


@pytest.mark.unit
def test_fallback_unit_is_none_when_witnesses_disagree():
    """见证图分类不一致 ⇒ 学不出映射,**判不出就说判不出**。"""
    pairs = [_p("a", "小歌剧厅2F", 4.8, unit="north"),
             _p("b", "小歌剧厅2F", 4.8, unit="south")]
    got = consensus_overrides(pairs)
    assert got[0]["fallback_unit"] is None


@pytest.mark.unit
def test_fallback_ignores_none_votes():
    """None 不参与投票 —— 「不知道」不该稀释一致性。"""
    pairs = [_p("a", "大歌剧厅3F", 10.3, unit=None),
             _p("b", "大歌剧厅3F", 10.3, unit="south")]
    got = consensus_overrides(pairs)
    assert got[0]["fallback_unit"] == "south"


# ── 从图名共现学「厅 → 区」映射(零硬编码词表) ───────────────────

@pytest.mark.unit
def test_alias_mapping_is_learned_from_titles():
    """**项目图纸自己写着答案**:图名「南区(大、中歌剧厅)…」的共现零歧义
    (实测 大:南10/北0、中:南125/北0、小:南0/北63)。

    别名来自楼层名、映射来自图名共现+分类器 —— 全数据驱动,
    不写「大歌剧厅=south」这种词表(用户约束:名称体系不得硬编码)。
    """
    from services.level_elevation_consensus import learn_unit_aliases

    # 注意真实语料两种写法并存:「大歌剧厅舞台图」(字面)与
    # 「南区（大、中歌剧厅）」(枚举缩写,字面不含「大歌剧厅」)。
    # 字面共现即可学出(实测 10 张字面含「大歌剧厅」且全在南区);
    # 枚举缩写展开是已知不做的边界 —— 语义解析的复杂度不值得。
    titled = [("南区大歌剧厅舞台平面图", "south")] * 3 + [
        ("北区（小歌剧厅）二层平面图", "north")] * 2
    got = learn_unit_aliases({"大歌剧厅", "小歌剧厅"}, titled)
    assert got == {"大歌剧厅": "south", "小歌剧厅": "north"}


@pytest.mark.unit
def test_ambiguous_alias_is_not_learned():
    """同一别名在两个区都出现 ⇒ 学不出,**判不出就说判不出**。"""
    from services.level_elevation_consensus import learn_unit_aliases

    titled = [("南区（歌剧厅）", "south"), ("北区（歌剧厅）", "north")]
    assert learn_unit_aliases({"歌剧厅"}, titled) == {}


@pytest.mark.unit
def test_unseen_alias_is_not_learned():
    from services.level_elevation_consensus import learn_unit_aliases

    assert learn_unit_aliases({"音乐厅"}, [("南区图", "south")]) == {}


@pytest.mark.unit
def test_split_handles_generic_venue_suffixes():
    """拆分按**场馆后缀**(厅/馆)泛化,不绑具体名字。"""
    assert split_unit_from_level("音乐厅2F") == ("音乐厅", "2F")
    assert split_unit_from_level("体育馆B1") == ("体育馆", "B1")


@pytest.mark.unit
def test_default_unit_does_not_veto_learning():
    """**默认兜底值没有否决权**:main 是「没匹配上」时给的默认
    (DEFAULT_UNIT_KEY),不是真实判定。实测「中歌剧厅」的票是
    south 125 / main 5 / None 14 —— 5 张 default 噪声不该否掉 125 张共识。
    """
    from services.level_elevation_consensus import learn_unit_aliases

    titled = [("南区中歌剧厅三层", "south")] * 5 + [("中歌剧厅详图", "main")]
    got = learn_unit_aliases({"中歌剧厅"}, titled, ignore_units={"main"})
    assert got == {"中歌剧厅": "south"}


@pytest.mark.unit
def test_two_real_units_still_veto():
    """真实单体之间的分歧仍然否决 —— 只豁免默认值,不放松一致性。"""
    from services.level_elevation_consensus import learn_unit_aliases

    titled = [("南区中歌剧厅", "south"), ("北区中歌剧厅", "north")]
    assert learn_unit_aliases({"中歌剧厅"}, titled, ignore_units={"main"}) == {}


@pytest.mark.unit
def test_shared_target_zone_is_not_remapped():
    """**实测倒退**:大、中歌剧厅都映到 south,而两厅同名层标高不同
    (F4: 16.1 vs 14.5)——挤进同一单体互相打架,把链式原本能出的键
    也炸掉了(合并后 10 层反而变 8 层)。

    楼层表的粒度装不下两个厅,硬塞就是赌。
    **目标区被多个别名共享 ⇒ 歧义,一个都不映**;独占目标才安全。
    """
    from services.level_elevation_consensus import learn_unit_aliases

    titled = ([("南区大歌剧厅", "south")] * 3 + [("南区中歌剧厅", "south")] * 3
              + [("北区小歌剧厅", "north")] * 3)
    got = learn_unit_aliases({"大歌剧厅", "中歌剧厅", "小歌剧厅"}, titled)
    assert got == {"小歌剧厅": "north"}, "south 被两厅共享,不得映射"
