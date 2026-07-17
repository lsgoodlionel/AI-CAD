"""
core/model3d/component_labels.py 单测(Phase E 路径C-下一步)

档案 OCR 短标签 → 构件类型(钢构/幕墙/围护桩…),经 A1 变换就近关联到几何构件。
关键:过滤噪声(只收短标签、排除 note/title 长句)。
"""
from services.drawing_transform import DrawingTransform
from core.model3d.component_labels import (
    attach_type_labels,
    classify_component_labels,
)


def _item(content, x, y, category="room_name"):
    return {"content": content, "location_json": {"bbox": [x, y, x + 10, y + 10]},
            "category": category}


def test_classify_recognizes_component_labels():
    t = DrawingTransform(scale_m_pt=0.01, origin_x=0.0, origin_y=0.0, page_h=1000.0)
    items = [
        _item("钢立柱", 100, 500),
        _item("幕墙", 200, 500),
        _item("围护桩", 300, 500),
    ]
    labels = classify_component_labels(items, t)
    types = {lab["type"] for lab in labels}
    assert "steel" in types
    assert "curtain_wall" in types
    assert "pile" in types
    # 坐标转米(bbox 中心 (105,505) → x=1.05, y=(1000-505)*0.01=4.95)
    steel = next(l for l in labels if l["type"] == "steel")
    assert abs(steel["x"] - 1.05) < 1e-6


def test_classify_filters_note_noise():
    t = DrawingTransform(scale_m_pt=0.01, origin_x=0.0, origin_y=0.0, page_h=1000.0)
    items = [
        _item("钢筋连接应满足规范要求见说明", 100, 500, category="note"),  # note 长句
        _item("纵向钢筋锚固长度", 150, 500, category="note"),
        _item("钢立柱", 200, 500, category="room_name"),                  # 真标签
    ]
    labels = classify_component_labels(items, t)
    # 只保留短的构件标签,note 长句被滤
    assert len(labels) == 1
    assert labels[0]["type"] == "steel"


def test_attach_assigns_nearest_label():
    # 两个构件,一个近钢标签、一个近幕墙标签
    elements = {
        "columns": [{"outline": [[0.9, 4.9], [1.1, 4.9], [1.1, 5.1], [0.9, 5.1]]}],
        "walls": [{"path": [[5.0, 5.0], [6.0, 5.0]]}],
    }
    labels = [
        {"type": "steel", "label": "钢立柱", "x": 1.0, "y": 5.0},
        {"type": "curtain_wall", "label": "幕墙", "x": 5.5, "y": 5.0},
    ]
    out = attach_type_labels(elements, labels, tol_m=1.0)
    assert out["columns"][0]["type_label"] == "steel"
    assert out["walls"][0]["type_label"] == "curtain_wall"


def test_attach_skips_far_labels():
    elements = {"columns": [{"outline": [[0.0, 0.0], [0.2, 0.0], [0.2, 0.2], [0.0, 0.2]]}]}
    labels = [{"type": "steel", "label": "钢立柱", "x": 50.0, "y": 50.0}]
    out = attach_type_labels(elements, labels, tol_m=1.0)
    assert "type_label" not in out["columns"][0]  # 太远不关联


def test_classify_rejects_cross_reference_notes():
    from services.drawing_transform import DrawingTransform
    t = DrawingTransform(scale_m_pt=0.01, origin_x=0.0, origin_y=0.0, page_h=1000.0)
    items = [
        _item("详幕墙深化", 100, 500),      # 交叉引用,非构件
        _item("见钢结构图", 150, 500),      # 交叉引用
        _item("钢立柱", 200, 500),          # 真标签
    ]
    labels = classify_component_labels(items, t)
    assert len(labels) == 1 and labels[0]["type"] == "steel"
