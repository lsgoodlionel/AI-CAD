"""Phase H COCO 导出:人审确认的构件金标签 → COCO 训练格式,喂 C-09 微调闭环。

数据飞轮:H4+ 人审 confirm/reclass 把构件转 confirmed(review_state)→ 本模块把这些
金标签(构件类型 + 在图纸上的归一化 bbox)导出 COCO → 训练识别器 → 下批更准。

- outline_to_norm_bbox:构件米坐标轮廓 → 图纸归一化 bbox(经 drawing_transform 逆变换);
- to_coco:金标签行 → COCO dict。均纯函数,离线可测。
"""
from __future__ import annotations

from typing import Any

# 构件类型 → COCO category_id(与 migration 033 type 对齐)
_CATEGORY_ID: dict[str, int] = {
    "column": 1, "pile": 2, "wall": 3, "beam": 4, "slab": 5,
    "pipe": 6, "equipment": 7, "door": 8, "window": 9,
}


def outline_to_norm_bbox(
    outline_m: list, scale: float, origin_x: float, origin_y: float, page_h: float,
) -> list[float] | None:
    """构件轮廓(米)→ 图纸归一化 bbox [x,y,w,h](同除 page_h,与 overlay 一致)。

    逆变换:x_pt=x_m/scale+ox;y_pt=page_h-(y_m/scale+oy)。无效轮廓/参数返回 None。
    """
    if not outline_m or not scale or not page_h:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for p in outline_m:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        x_pt = float(p[0]) / scale + origin_x
        y_pt = page_h - (float(p[1]) / scale + origin_y)
        xs.append(x_pt / page_h)
        ys.append(y_pt / page_h)
    if not xs:
        return None
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def to_coco(rows: list[dict], *, project_id: str, exported_at: str | None = None) -> dict[str, Any]:
    """构件金标签行 [{drawing_id, category, bbox:[x,y,w,h]}] → COCO dict。

    仅期望传入 confirmed 金标签。bbox 为归一化 [x,y,w,h]。exported_at 由调用方传入
    (纯函数不取系统时钟)。
    """
    images: list[dict] = []
    image_id_by_drawing: dict[str, int] = {}
    annotations: list[dict] = []

    for record in rows:
        drawing_id = str(record.get("drawing_id"))
        bbox = record.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        image_id = image_id_by_drawing.get(drawing_id)
        if image_id is None:
            image_id = len(images) + 1
            image_id_by_drawing[drawing_id] = image_id
            images.append({
                "id": image_id, "drawing_id": drawing_id, "file_name": f"{drawing_id}.png",
            })
        category = str(record.get("category") or "")
        annotations.append({
            "id": len(annotations) + 1,
            "image_id": image_id,
            "category_id": _CATEGORY_ID.get(category, 0),
            "category_name": category,
            "bbox": [round(float(v), 4) for v in bbox],
            "area": round(float(bbox[2]) * float(bbox[3]), 6),
            "iscrowd": 0,
            "normalized": True,     # bbox 为页面归一化坐标(非像素)
        })

    return {
        "info": {
            "project_id": project_id,
            "source": "Phase H component gold labels",
            "description": "构件级金标签(仅人审 confirmed),归一化 bbox,喂 C-09 训练",
            "exported_at": exported_at,
        },
        "categories": [{"id": cid, "name": name} for name, cid in _CATEGORY_ID.items()],
        "images": images,
        "annotations": annotations,
    }
