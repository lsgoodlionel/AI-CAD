"""
model_elements.archive_axes_to_scene 单测(Phase E 路径C-A2)

档案 axis 项(label + pt 位置)经变换 → scene 轴网格式({x/y:[[label,pos_m]]})。
方向按轴号惯例:数字→x(竖轴),字母→y(横轴)。
"""
from services.drawing_transform import DrawingTransform
from services.model_elements import archive_axes_to_scene


def _item(label, x, y):
    return {"content": label, "location_json": {"x": x, "y": y}}


def test_digits_go_x_letters_go_y():
    t = DrawingTransform(scale_m_pt=0.01, origin_x=0.0, origin_y=0.0, page_h=1000.0)
    items = [_item("1", 100.0, 500.0), _item("2", 200.0, 500.0),
             _item("A", 300.0, 100.0), _item("B", 300.0, 200.0)]
    axes = archive_axes_to_scene(items, t)

    x_labels = [lab for lab, _ in axes["x"]]
    y_labels = [lab for lab, _ in axes["y"]]
    assert set(x_labels) == {"1", "2"}      # 数字轴号 → x
    assert set(y_labels) == {"A", "B"}      # 字母轴号 → y
    # 坐标转米:x=(100-0)*0.01=1.0 ; y=((1000-100)-0)*0.01=9.0
    assert dict(axes["x"])["1"] == 1.0
    assert dict(axes["y"])["A"] == 9.0


def test_junk_labels_dropped():
    t = DrawingTransform(scale_m_pt=0.01, origin_x=0.0, origin_y=0.0, page_h=1000.0)
    items = [_item("1", 100.0, 500.0), _item("说明文字噪声很长", 150.0, 500.0)]
    axes = archive_axes_to_scene(items, t)
    assert [lab for lab, _ in axes["x"]] == ["1"]


def test_empty_items_yield_empty_axes():
    t = DrawingTransform(scale_m_pt=0.01, origin_x=0.0, origin_y=0.0, page_h=1000.0)
    axes = archive_axes_to_scene([], t)
    assert axes == {"x": [], "y": []}
