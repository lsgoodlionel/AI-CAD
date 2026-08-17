"""剖面/立面标高线性拟合单测 —— 标高↔图上位置自校验。纯函数。"""
from services.section_elevation_fit import (
    fit_elevation_axis,
    main_story_elevations,
)


def _line(pairs):
    return [(y, e) for y, e in pairs]


def test_fit_perfect_line_is_trusted():
    """剖面竖向按比例绘制 → 标高与 y 严格线性(实测东立面图即如此)。"""
    pts = [(1348.6, 0.0), (1225.6, 10.04), (1102.0, 20.12), (1032.7, 25.78)]
    r = fit_elevation_axis(pts)
    assert r["ok"] is True
    assert r["r_squared"] > 0.99
    assert r["slope"] < 0            # 图上 y 越小(越靠上)标高越大


def test_fit_drops_outliers():
    """偏离拟合线的(OCR 误识/局部标高)被剔除,剩余仍可信。"""
    pts = [(1000.0, 0.0), (900.0, 5.0), (800.0, 10.0), (700.0, 15.0), (650.0, 99.0)]
    r = fit_elevation_axis(pts)
    assert r["dropped"] >= 1
    assert r["ok"] is True


def test_fit_rejects_non_linear_data():
    """数据整体不线性 → 判不可信(宁可不给,不给错的)。"""
    pts = [(100.0, 0.0), (200.0, 9.0), (300.0, 2.0), (400.0, 25.0), (500.0, 1.0)]
    r = fit_elevation_axis(pts)
    assert r["ok"] is False


def test_fit_too_few_points():
    assert fit_elevation_axis([(1.0, 1.0)])["ok"] is False
    assert fit_elevation_axis([])["ok"] is False


def test_main_story_sequence_filters_local_elevations():
    """楼面标高的特征是相邻间距为合理层高;窗顶等局部标高被排除。"""
    elevations = [0.0, 1.2, 2.1, 4.5, 5.3, 9.0, 13.5]   # 1.2/2.1/5.3 是局部
    seq = main_story_elevations(elevations)
    assert seq[0] == 0.0
    assert 4.5 in seq and 9.0 in seq and 13.5 in seq
    assert 1.2 not in seq


def test_main_story_handles_empty():
    assert main_story_elevations([]) == []


def test_main_story_dedupes_and_sorts():
    seq = main_story_elevations([9.0, 0.0, 4.5, 4.5, 0.0])
    assert seq == [0.0, 4.5, 9.0]


def test_match_to_floors_nearest_within_tolerance():
    """恢复标高按最近邻匹配楼层现值;超容差不匹配(该层不在此剖面覆盖内)。"""
    from services.section_elevation_fit import match_to_floors
    floors = [{"story_key": "B1", "order": -1, "elevation_m": -4.2},
              {"story_key": "F1", "order": 1, "elevation_m": 0.0},
              {"story_key": "F9", "order": 9, "elevation_m": 99.0}]
    out = match_to_floors([-5.2, 0.8, 4.9], floors)
    assert out["B1"]["elevation_m"] == -5.2
    assert abs(out["B1"]["delta_m"] + 1.0) < 1e-6
    assert out["F1"]["elevation_m"] == 0.8
    assert "F9" not in out          # 99.0 无近邻 → 不给


def test_match_each_value_used_once():
    from services.section_elevation_fit import match_to_floors
    floors = [{"story_key": "A", "order": 1, "elevation_m": 0.0},
              {"story_key": "B", "order": 2, "elevation_m": 0.1}]
    out = match_to_floors([0.05], floors)
    assert len(out) == 1            # 同一标高不重复分配


def test_match_empty():
    from services.section_elevation_fit import match_to_floors
    assert match_to_floors([], [{"story_key": "A", "order": 1, "elevation_m": 0}]) == {}
