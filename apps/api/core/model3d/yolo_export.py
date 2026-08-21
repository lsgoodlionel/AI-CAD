"""既有识别结果 → YOLO 训练集。

**为什么用识别器输出而不是图层弱标签**：图层弱标签（Phase C 的
`auto_label`）实测命中率只有 6.6%（§8.6，图层命名不规范），
而确定性识别器的输出是现成的 **340 张图 / 76858 个框**，带类别带轮廓。

**但标注质量必须先验**：识别器有已知错误模式（本轮修过钢筋图层的
3410 个假柱、516 面本该是梁的墙）。在错标注上训练等于教模型复制错误，
所以导出之后、训练之前要人工抽检。
"""
from __future__ import annotations

#: 类别顺序**必须稳定**：训练好的权重按 id 索引类别，
#: 顺序一变，模型输出的「柱」就成了「墙」。
#: 与 Phase C 的 9 类体系一致（`data/model3d/layer_class_map.yaml`），不另起一套。
CLASS_NAMES = ["column", "wall", "beam", "slab", "pipe", "equipment",
               "door", "window", "axis"]

#: scene 里的复数命名 → 类别名。
_KIND_TO_CLASS = {
    "columns": "column", "walls": "wall", "beams": "beam", "slabs": "slab",
    "pipes": "pipe", "equipment": "equipment", "doors": "door",
    "windows": "window", "axes": "axis",
}


def class_id(kind: str) -> int | None:
    """构件类别 → YOLO 类别 id；认不出返回 None（**不编一个 id**）。"""
    name = _KIND_TO_CLASS.get(str(kind))
    return CLASS_NAMES.index(name) if name in CLASS_NAMES else None


def outline_to_yolo_box(points: list | None, page_w: float,
                        page_h: float) -> tuple | None:
    """轮廓 → 归一化的 (cx, cy, w, h)。

    零面积的框是噪声，超出页面的框多半是坐标算错了
    （本轮实测有单图跨 4176 米的）——两者都丢弃：
    **宁可少一个样本，不要一个假样本**。
    """
    pts = [(float(p[0]), float(p[1])) for p in (points or [])
           if isinstance(p, (list, tuple)) and len(p) >= 2]
    if len(pts) < 2 or page_w <= 0 or page_h <= 0:
        return None
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    if x0 < 0 or y0 < 0 or x1 > page_w or y1 > page_h:
        return None
    return ((x0 + x1) / 2 / page_w, (y0 + y1) / 2 / page_h,
            (x1 - x0) / page_w, (y1 - y0) / page_h)


def label_lines(elements: list | None, page_w: float, page_h: float) -> list[str]:
    """构件列表 → YOLO 标注行（`cls cx cy w h`）。"""
    lines = []
    for element in elements or []:
        cid = class_id(element.get("kind"))
        if cid is None:
            continue
        box = outline_to_yolo_box(
            element.get("outline") or element.get("path"), page_w, page_h)
        if box is None:
            continue
        lines.append(f"{cid} " + " ".join(f"{v:.6f}" for v in box))
    return lines


def meters_to_page(x_m: float, y_m: float, scale_m_pt: float,
                   origin_pt: tuple, page_h: float) -> tuple:
    """米 → 页面点，与 `_Ctx.to_m` 严格互逆。

    **必须用识别器自己的那组参数**（`FloorElements.scale/origin_pt/page_h`），
    不能用 `drawing_transform`——构件坐标压根不走那张表
    （本轮实测：修好某图的 drawing_transform 后构件坐标纹丝不动）。
    用错参数的后果实测过：叠框核验时真正的柱子一个没框上，
    几个框挤在图幅左边缘。
    """
    if scale_m_pt <= 0:
        return (0.0, 0.0)
    ox, oy = float(origin_pt[0]), float(origin_pt[1])
    x_pt = x_m / scale_m_pt + ox
    y_pt = page_h - (y_m / scale_m_pt + oy)
    return (x_pt, y_pt)
