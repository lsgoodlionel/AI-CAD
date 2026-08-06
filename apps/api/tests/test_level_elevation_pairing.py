"""楼层名 ↔ 标高 配对。

**为什么需要**:模型 v31 的 13 层里 10 层标高是硬编码 `DEFAULT_STORY_HEIGHT_M = 4.5`
推出来的,与图纸实测差到 **11.9 米**:

| 层 | 模型 | 图纸真值(A-20-02A 南立面标高链) | 差 |
|---|---:|---:|---:|
| F5 设备层 | 20.4 | 26.900 | −6.5 |
| F6 设备层 | 24.9 | 36.800 | −11.9 |
| RF 屋顶 | 33.9 | 45.500 | −11.6 |

而图纸上楼层名与标高**就写在同一行**,档案层也已经存了 bbox。
把它们配起来,就能得到**真实的楼层标高表**,替掉默认值。

国标依据:GB/T 50001 §11.8.3「标高数字应注写在标高符号的上侧或下侧」
—— 标高数字与其名称处在同一水平线上,这是配对的几何依据。

实测样本(A-20-02A):
```
elevation  36.800     bbox y∈[240.84, 245.88]  x∈[128.16, 139.68]
level_name 6F（设备层  bbox y∈[243.00, 250.20]  x∈[ 98.64, 124.56]
```
y 中心差 3.2pt,楼层名在标高左侧,x 间隙 3.6pt。
"""
from __future__ import annotations

import pytest

from services.level_elevation_pairing import pair_levels_with_elevations


def _item(content: str, x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"content": content, "location_json": {"bbox": [x0, y0, x1, y1]}}


#: 直接取自 A-20-02A 的实测 bbox
REAL_ELEV = [
    _item("16.100", 126.36, 487.80, 139.68, 493.56),
    _item("26.900", 127.44, 358.56, 139.68, 364.32),
    _item("36.800", 128.16, 240.84, 139.68, 245.88),
    _item("45.300", 886.32, 135.36, 905.04, 142.92),   # 远在图面另一侧
]
REAL_LEVELS = [
    _item("6F（设备层", 98.64, 243.00, 124.56, 250.20),
    _item("5F31.300夹层（设备层）3.300", 68.76, 304.92, 140.40, 316.44),
    _item("5F（设备层）", 1883.52, 1091.16, 1911.24, 1099.80),  # 图例区,不该配上
]


@pytest.mark.unit
def test_pairs_level_name_with_elevation_on_the_same_row():
    pairs = pair_levels_with_elevations(REAL_ELEV, REAL_LEVELS)
    got = {p["level_name"]: p["elevation_m"] for p in pairs}
    assert got.get("6F（设备层") == pytest.approx(36.800)


@pytest.mark.unit
def test_far_away_level_name_is_not_paired():
    """图例区的 `5F（设备层）` 在 x=1883,离标高链 1700pt 远,不能配上。"""
    pairs = pair_levels_with_elevations(REAL_ELEV, REAL_LEVELS)
    for p in pairs:
        assert p["level_name"] != "5F（设备层）"


@pytest.mark.unit
def test_elevation_without_a_level_name_is_not_invented():
    """`45.300` 附近没有楼层名 —— 不能硬凑一个。"""
    pairs = pair_levels_with_elevations(REAL_ELEV, REAL_LEVELS)
    assert all(p["elevation_m"] != pytest.approx(45.300) for p in pairs)


@pytest.mark.unit
def test_pairing_is_one_to_one():
    """一个楼层名只配一个标高,一个标高只配一个楼层名。"""
    elev = [_item("10.000", 200, 100, 220, 106),
            _item("10.500", 200, 103, 220, 109)]   # 两个标高挨得很近
    levels = [_item("3F", 170, 101, 190, 108)]
    pairs = pair_levels_with_elevations(elev, levels)
    assert len(pairs) == 1
    assert pairs[0]["level_name"] == "3F"


@pytest.mark.unit
def test_level_name_may_sit_on_either_side():
    """楼层名在标高左侧或右侧都可以 —— 实测两种写法都有。"""
    left = pair_levels_with_elevations(
        [_item("5.400", 200, 100, 230, 106)], [_item("2F", 170, 100, 195, 106)])
    right = pair_levels_with_elevations(
        [_item("5.400", 200, 100, 230, 106)], [_item("2F", 235, 100, 260, 106)])
    assert left and right
    assert left[0]["elevation_m"] == right[0]["elevation_m"] == pytest.approx(5.4)


@pytest.mark.unit
def test_missing_bbox_is_skipped_not_crashed():
    pairs = pair_levels_with_elevations(
        [{"content": "5.400"}], [{"content": "2F", "location_json": {}}])
    assert pairs == []


@pytest.mark.unit
def test_unparsable_elevation_is_skipped():
    """`±0.000` 要能解析;垃圾文本要跳过而不是抛异常。"""
    pairs = pair_levels_with_elevations(
        [_item("±0.000", 200, 100, 230, 106), _item("abc", 200, 200, 230, 206)],
        [_item("1F", 170, 100, 195, 106), _item("2F", 170, 200, 195, 206)])
    assert len(pairs) == 1
    assert pairs[0]["level_name"] == "1F"
    assert pairs[0]["elevation_m"] == pytest.approx(0.0)


@pytest.mark.unit
def test_result_is_sorted_by_elevation():
    pairs = pair_levels_with_elevations(
        [_item("36.800", 200, 100, 230, 106), _item("5.400", 200, 300, 230, 306)],
        [_item("6F", 170, 100, 195, 106), _item("2F", 170, 300, 195, 306)])
    assert [p["elevation_m"] for p in pairs] == [pytest.approx(5.4),
                                                 pytest.approx(36.8)]


# ── 标高链:只在链上配对 ──────────────────────────────────────────

@pytest.mark.unit
def test_only_pairs_on_the_main_elevation_chain():
    """立面图的标高**竖向排成一列**(x 相近、y 递变)。

    **实测缺口**:A-20-02A 右侧图例/索引区(x≈1883)也有 `5F（设备层）`
    `6F（设备层）` 字样,旁边恰好有别的标高数字,于是配出
    `5F（设备层）→22.000`、`6F（设备层）→29.800` 两个**错值**
    (真值 26.900 / 36.800)。

    判据:先按 x 找出成列的主标高链(≥3 个),只在链上配对。
    """
    from services.level_elevation_pairing import main_elevation_chain

    chain_items = [_item("5.400", 200, 500, 230, 506),
                   _item("10.800", 200, 400, 230, 406),
                   _item("16.200", 200, 300, 230, 306),
                   _item("21.890", 200, 200, 230, 206)]
    stray = [_item("22.000", 1880, 350, 1910, 356),   # 图例区孤立标高
             _item("29.800", 1880, 250, 1910, 256)]
    chain = main_elevation_chain(chain_items + stray)
    values = {c["elevation_m"] for c in chain}
    assert values == {5.4, 10.8, 16.2, 21.89}
    assert 22.0 not in values and 29.8 not in values


@pytest.mark.unit
def test_chain_needs_at_least_three_members():
    """两个孤立标高不成链 —— 不能把任意一对当主链。"""
    from services.level_elevation_pairing import main_elevation_chain

    assert main_elevation_chain([_item("5.400", 200, 500, 230, 506),
                                 _item("10.800", 200, 400, 230, 406)]) == []


@pytest.mark.unit
def test_pairing_restricted_to_chain_rejects_legend_area():
    """端到端:图例区的同名楼层名不再配出错标高。"""
    elev = [_item("5.400", 200, 500, 230, 506),
            _item("10.800", 200, 400, 230, 406),
            _item("16.200", 200, 300, 230, 306),
            _item("22.000", 1880, 350, 1910, 356)]     # 图例区
    levels = [_item("2F", 170, 500, 195, 506),
              _item("5F（设备层）", 1845, 350, 1875, 356)]   # 图例区同名
    pairs = pair_levels_with_elevations(elev, levels, chain_only=True)
    assert [(p["level_name"], p["elevation_m"]) for p in pairs] == [("2F", 5.4)]
