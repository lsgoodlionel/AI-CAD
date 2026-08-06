"""高位屋面(台塔/塔楼)与主体屋面必须分层。

**实测错误**:`A-10-10.1C 台塔屋顶平面图` 与 `A-10-10.2B 屋顶平面图`
都被归到同一个 `RF`,标高同为 33.9 —— 而它们是**两个不同的标高面**:

| 图 | 标高范围(纯数值标高实测) | 最高 |
|---|---|---:|
| A-10-10.1C 台塔屋顶 | 23.400 ~ 43.000 | **43.000** |
| A-10-10.2B 屋顶 | 2.500 ~ 28.180 | **28.180** |

差约 **15 米**。台塔是舞台塔楼,高出主体屋面一大截,
把两者堆到同一标高会让台塔整体塌下来 15 米。

判据是国标语境下的部位词:台塔/塔楼/塔台 —— 它们是主体之上的独立高位体量。
"""
from __future__ import annotations

import pytest

from services.floor_parser import HIGH_ROOF_FLOOR, ROOF_FLOOR, parse_floor


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "台塔屋顶平面图", "台塔设备屋面防水保温构造节点图", "塔楼屋面平面图",
    "舞台塔屋顶平面图",
])
def test_tower_roof_is_a_separate_high_floor(text):
    got = parse_floor(text)
    assert got is not None
    assert got[0] == HIGH_ROOF_FLOOR[0]
    assert got[0] != ROOF_FLOOR[0], "不能与主体屋面同层"


@pytest.mark.unit
@pytest.mark.parametrize("text", ["屋顶平面图", "屋面排水组织平面图", "屋面层平面"])
def test_plain_roof_stays_on_the_main_roof(text):
    assert parse_floor(text)[0] == ROOF_FLOOR[0]


@pytest.mark.unit
def test_high_roof_sorts_above_the_main_roof():
    """order 必须更大 —— 否则堆叠顺序仍会把台塔压在主体屋面下。"""
    assert HIGH_ROOF_FLOOR[2] > ROOF_FLOOR[2]


@pytest.mark.unit
def test_high_roof_is_still_a_roof_sentinel():
    """它仍是屋面哨兵,不该被当成普通楼层走线性标高。"""
    from services.model_story import _is_story_sentinel

    assert _is_story_sentinel(HIGH_ROOF_FLOOR[2])


@pytest.mark.unit
def test_tower_word_alone_is_not_a_roof():
    """只提「台塔」而不提屋面/屋顶的图,不该被判成屋面层。

    实测有 `台塔给排水及消防平面图（一）`、`地下室台仓…` 这类,
    它们是台塔的**其他楼层**,不是屋面。
    """
    got = parse_floor("台塔给排水及消防平面图（一）")
    assert got is None or got[0] not in (ROOF_FLOOR[0], HIGH_ROOF_FLOOR[0])
