"""密排阵列（座椅/吸声板/铺装单元）识别 —— 把它们从柱候选里摘出来。

实测依据（`data/model3d/gold/rule_vs_model_v1.json`，60 格独立判读）：
60 个柱候选里 28 格判为 seat，规则侧的 17 格只来自两张图。
在那两张图上实测候选排布：

* 二层平面图(五)：一行 30 个，x 间距 `0.36×5 → 1.15(走道) → 0.36×5`，边长 0.36m
* 三层平面图(三)：一行 21 个连续 0.37m，边长 0.47m

即**间距等于自身边长**。而真柱网的间距是柱宽的 6~9 倍
（一层结构平面图（四）实测比值中位 6.39，地下一层 6.78）。
「等间距成排」两者都满足，把它们分开的只有**间距与自身尺寸的比**，
所以两条判据必须同时成立，缺一条就会误杀柱网。
"""
import pytest

from core.model3d.dense_array_filter import (
    DEFAULT_GAP_RATIO_MAX, DEFAULT_RUN_MIN, find_dense_array_flags,
)


def _kept(elements: list[dict]) -> list[dict]:
    flags = find_dense_array_flags(elements)
    return [e for e, f in zip(elements, flags) if not f]


def _sq(cx: float, cy: float, side: float) -> dict:
    """以 (cx, cy) 为心、边长 side 的方形构件（米制 outline）。"""
    h = side / 2
    return {"outline": [[cx - h, cy - h], [cx + h, cy - h],
                        [cx + h, cy + h], [cx - h, cy + h]], "src": "t"}


def _row(n: int, *, gap: float, side: float, y: float = 0.0, x0: float = 0.0) -> list[dict]:
    return [_sq(x0 + i * gap, y, side) for i in range(n)]


class TestSeatArrays:
    """实测阳性形态必须被识别出来。"""

    def test_seat_row_is_flagged(self):
        """一排紧挨的座椅（间距=边长，实测形态）整排被标记。"""
        seats = _row(8, gap=0.36, side=0.36)
        assert find_dense_array_flags(seats) == [True] * 8

    def test_seat_row_along_y_is_flagged(self):
        """沿 y 成排同样被识别 —— 图纸朝向不该改变结论。"""
        seats = [_sq(0.0, i * 0.36, 0.36) for i in range(8)]
        assert find_dense_array_flags(seats) == [True] * 8

    def test_aisle_split_row_still_flagged_per_segment(self):
        """走道打断后每段仍够长（实测 5 个一组），两段都标记。"""
        seats = _row(6, gap=0.36, side=0.36) + _row(6, gap=0.36, side=0.36, x0=3.0)
        assert find_dense_array_flags(seats) == [True] * 12

    def test_wide_seat_block_flagged(self):
        """三层平面图(三) 形态：边长 0.47、间距 0.37（间距略小于边长）。"""
        seats = _row(10, gap=0.37, side=0.47)
        assert all(find_dense_array_flags(seats))


class TestColumnGridSurvives:
    """真柱网必须一根不少 —— 这是本判据的误伤红线。"""

    def test_column_grid_not_flagged(self):
        """8.4m 轴距、0.6m 柱：间距是柱宽的 14 倍，全部保留。"""
        cols = _row(10, gap=8.4, side=0.6)
        assert find_dense_array_flags(cols) == [False] * 10

    def test_tight_column_grid_not_flagged(self):
        """实测最密的真柱网比值 ~3.8（10 分位），仍须全部保留。"""
        cols = _row(10, gap=0.6 * 3.8, side=0.6)
        assert not any(find_dense_array_flags(cols))

    def test_mixed_plan_drops_seats_keeps_columns(self):
        """同一张图上座椅与柱共存 —— 只删座椅，柱一根不动。

        观众厅平面图正是这种图：判据若按整张图删就会连柱一起删。
        """
        seats = _row(9, gap=0.36, side=0.36, y=0.0)
        cols = _row(4, gap=8.4, side=0.6, y=20.0)
        assert _kept(seats + cols) == cols

    def test_short_run_not_flagged(self):
        """只有 4 个挨着的候选够不上「成排」，不删 —— 偶发相邻不是阵列。"""
        seats = _row(DEFAULT_RUN_MIN - 1, gap=0.36, side=0.36)
        assert not any(find_dense_array_flags(seats))


class TestUnevenSpacing:
    def test_irregular_spacing_not_flagged(self):
        """间距忽大忽小不是阵列 —— 阵列的本义是「规则」。"""
        xs = [0.0, 0.36, 1.10, 1.50, 3.20, 3.60]
        items = [_sq(x, 0.0, 0.36) for x in xs]
        assert not all(find_dense_array_flags(items))


class TestRobustness:
    """降级必须可见、缺失不得阻断（`MODELING_PIPELINE_BLUEPRINT.md` §7）。"""

    @pytest.mark.parametrize("items", [[], [_sq(0, 0, 0.4)]])
    def test_trivial_input(self, items):
        assert find_dense_array_flags(items) == [False] * len(items)
        assert _kept(items) == items

    def test_degenerate_outline_survives(self):
        """轮廓点不足/退化的元素既不崩，也不被误删。"""
        bad = [{"outline": [], "src": "t"}, {"outline": [[1.0, 1.0]], "src": "t"},
               {"src": "t"}]
        assert find_dense_array_flags(bad) == [False, False, False]
        assert _kept(bad) == bad

    def test_zero_size_element_not_flagged(self):
        """零尺寸元素的「间距/边长」会除零 —— 必须不参与阵列判定。"""
        items = [{"outline": [[0.0, 0.0]] * 4, "src": "t"} for _ in range(8)]
        assert not any(find_dense_array_flags(items))

    def test_input_not_mutated(self):
        """判据只读，不得改动入参（全局不可变纪律）。"""
        seats = _row(8, gap=0.36, side=0.36)
        snapshot = [dict(s) for s in seats]
        find_dense_array_flags(seats)
        assert seats == snapshot


class TestThresholdsAreExplicit:
    def test_defaults_match_measured_separation(self):
        """默认阈值必须落在实测的分离带内，改动阈值要先改这条测试。

        实测：阳性图比值中位 0.94/1.00，结构图比值 10 分位 3.83。
        """
        assert 1.0 < DEFAULT_GAP_RATIO_MAX < 3.8
        assert DEFAULT_RUN_MIN >= 5
