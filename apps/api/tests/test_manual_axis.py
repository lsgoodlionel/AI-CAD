"""人工标定轴线基准单测(纯函数部分)。"""
from services.manual_axis import axis_position, scale_from_spacing, to_scene_axes


def _ref(label, direction, p, spacing=None):
    """构造一条标准轴线:direction='x' 竖线(x 固定),'y' 横线(y 固定)。"""
    if direction == "x":
        return {"label": label, "direction": "x", "x1_norm": p, "y1_norm": 0.1,
                "x2_norm": p, "y2_norm": 0.9, "spacing_to_prev_mm": spacing}
    return {"label": label, "direction": "y", "x1_norm": 0.1, "y1_norm": p,
            "x2_norm": 0.9, "y2_norm": p, "spacing_to_prev_mm": spacing}


def test_axis_position_vertical_and_horizontal():
    assert axis_position(_ref("1", "x", 0.3)) == 0.3
    assert axis_position(_ref("A", "y", 0.6)) == 0.6


def test_axis_position_rejects_skewed_line():
    """标歪的线(不够垂直/水平)不可用作基准。"""
    skew = {"label": "1", "direction": "x", "x1_norm": 0.2, "y1_norm": 0.1,
            "x2_norm": 0.5, "y2_norm": 0.9}
    assert axis_position(skew) is None


def test_scale_from_spacing_computes_ratio():
    """人标两条轴线 + 实际轴距 8400mm → 直接反算比例尺(比读文字可靠)。"""
    page_h = 1000.0
    refs = [_ref("1", "x", 0.10), _ref("2", "x", 0.20, spacing=8400)]
    got = scale_from_spacing(refs, page_h)
    # 图上距离 = 0.1 × 1000 = 100pt;8.4m / 100pt = 0.084 m/pt
    assert abs(got["scale_m_pt"] - 0.084) < 1e-6
    assert got["samples"] == 1


def test_scale_from_spacing_multiple_samples_median():
    page_h = 1000.0
    refs = [_ref("1", "x", 0.10), _ref("2", "x", 0.20, spacing=8400),
            _ref("3", "x", 0.30, spacing=8400)]
    got = scale_from_spacing(refs, page_h)
    assert got["samples"] == 2
    assert got["spread"] == 0.0          # 两样本一致 → 离散度 0(高可信)


def test_scale_from_spacing_without_spacing_returns_none():
    assert scale_from_spacing([_ref("1", "x", 0.1), _ref("2", "x", 0.2)], 1000.0) is None


def test_scale_from_spacing_no_page_height():
    assert scale_from_spacing([_ref("1", "x", 0.1)], 0) is None


def test_to_scene_axes_converts_to_meters():
    """人工轴线 → scene 轴网(米坐标,与构件同系)。"""
    class _T:
        scale_m_pt = 0.1
        origin_x = 0.0
        origin_y = 0.0
        page_h = 1000.0
    axes = to_scene_axes([_ref("1", "x", 0.2), _ref("2", "x", 0.5),
                          _ref("A", "y", 0.3)], _T())
    assert [e["label"] for e in axes["x"]] == ["1", "2"]
    assert len(axes["y"]) == 1
    assert axes["x"][0]["coord"] < axes["x"][1]["coord"]     # 按坐标排序


def test_to_scene_axes_without_page_height():
    class _T:
        page_h = 0
    assert to_scene_axes([_ref("1", "x", 0.2)], _T()) == {"x": [], "y": []}
