"""轴网合理性校验单测 —— 防「轴线远离模型主体」。纯函数。"""
from services.axes_validation import (
    axes_bounds,
    axes_plausible,
    elements_bounds,
    filter_scene_axes,
)


def _elements(x0=0.0, x1=120.0, y0=0.0, y1=100.0):
    return {"columns": [{"outline": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}],
            "walls": [], "beams": [], "slabs": [], "pipes": [], "equipment": []}


def _axes(x0, x1, y0, y1):
    return {"x": [{"label": "1", "coord": x0}, {"label": "9", "coord": x1}],
            "y": [{"label": "A", "coord": y0}, {"label": "H", "coord": y1}]}


def test_bounds_helpers():
    assert elements_bounds(_elements()) == (0.0, 120.0, 0.0, 100.0)
    assert axes_bounds(_axes(5, 100, 5, 90)) == (5, 100, 5, 90)
    assert elements_bounds({"columns": []}) is None
    assert axes_bounds({"x": [{"coord": 1}], "y": []}) is None   # 单向不算


def test_plausible_when_axes_overlap_elements():
    ok, _ = axes_plausible(_axes(5, 110, 5, 95), _elements())
    assert ok is True


def test_reject_axes_far_away():
    """实测 B1:轴网 x[-76,960] y[720,1306] vs 构件 x[-4,123] y[-35,144] → 必须剔除。"""
    ok, reason = axes_plausible(_axes(-76, 960, 720, 1306), _elements())
    assert ok is False
    assert "远离" in reason


def test_reject_axes_too_small_span():
    """局部详图轴网(跨度过小)不能代表整层。"""
    ok, reason = axes_plausible(_axes(10, 15, 10, 14), _elements())
    assert ok is False
    assert "跨度过小" in reason


def test_axes_may_extend_beyond_elements_slightly():
    """轴线常越出建筑轮廓一段作标注 → 合理外扩应被接受。"""
    ok, _ = axes_plausible(_axes(-15, 135, -12, 112), _elements())
    assert ok is True


def test_reject_single_direction_axes():
    ok, reason = axes_plausible({"x": [{"coord": 5}], "y": []}, _elements())
    assert ok is False
    assert "非双向" in reason


def test_filter_scene_axes_drops_bad_keeps_good():
    floors = [
        {"key": "OK", "elements": _elements(), "axes": _axes(5, 110, 5, 95)},
        {"key": "BAD", "elements": _elements(), "axes": _axes(-76, 960, 720, 1306)},
        {"key": "NONE", "elements": _elements(), "axes": None},
    ]
    stat = filter_scene_axes(floors)
    assert stat["kept"] == 1
    assert stat["dropped"] == 1
    assert floors[0]["axes"] is not None       # 好的保留
    assert floors[1]["axes"] is None           # 坏的剔除(宁可无轴网)
    assert stat["details"][0]["floor"] == "BAD"


def test_manual_axes_bypass_validation():
    """人工标定基准是人核过的真值,不被自动校验否决。"""
    floors = [{"key": "M", "elements": _elements(), "axes_source": "manual",
               "axes": _axes(-999, 999, -999, 999)}]     # 自动校验本会判「远离」
    stat = filter_scene_axes(floors)
    assert stat["dropped"] == 0
    assert floors[0]["axes"] is not None
