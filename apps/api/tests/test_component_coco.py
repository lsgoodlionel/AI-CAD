"""Phase H COCO 导出单测 —— outline→归一化 bbox / to_coco。纯函数。"""
from services.component_coco import _CATEGORY_ID, outline_to_norm_bbox, to_coco


def test_outline_to_norm_bbox_inverse_transform():
    # 正方形轮廓(米),scale/origin/page_h 取实测量级
    outline = [[21.0, 17.0], [21.5, 17.5]]
    bbox = outline_to_norm_bbox(outline, 0.0261535, 1188.26, 143.48, 2384.0)
    assert bbox is not None
    x, y, w, h = bbox
    assert 0 <= x <= 1 and w > 0 and h > 0     # 归一化正框


def test_outline_bbox_invalid_returns_none():
    assert outline_to_norm_bbox([], 0.02, 1, 1, 2384) is None
    assert outline_to_norm_bbox([[1, 1]], 0, 1, 1, 2384) is None   # scale=0
    assert outline_to_norm_bbox([[1, 1]], 0.02, 1, 1, 0) is None   # page_h=0


def test_to_coco_structure():
    rows = [
        {"drawing_id": "d1", "category": "column", "bbox": [0.1, 0.2, 0.05, 0.05]},
        {"drawing_id": "d1", "category": "wall", "bbox": [0.3, 0.3, 0.2, 0.02]},
        {"drawing_id": "d2", "category": "pile", "bbox": [0.5, 0.5, 0.04, 0.04]},
    ]
    coco = to_coco(rows, project_id="p1", exported_at="2026-07-17T00:00:00Z")
    assert len(coco["images"]) == 2            # d1, d2 去重
    assert len(coco["annotations"]) == 3
    assert coco["annotations"][0]["category_id"] == _CATEGORY_ID["column"]
    assert coco["annotations"][0]["normalized"] is True
    assert coco["images"][0]["file_name"] == "d1.png"
    # 同图纸的标注共用 image_id
    a0, a1 = coco["annotations"][0], coco["annotations"][1]
    assert a0["image_id"] == a1["image_id"]


def test_to_coco_skips_bad_bbox():
    rows = [{"drawing_id": "d1", "category": "column", "bbox": [0.1, 0.2]}]   # 长度错
    coco = to_coco(rows, project_id="p1")
    assert coco["annotations"] == []


def test_categories_cover_all_types():
    coco = to_coco([], project_id="p1")
    names = {c["name"] for c in coco["categories"]}
    assert {"column", "pile", "wall", "beam", "slab", "pipe", "equipment"} <= names
