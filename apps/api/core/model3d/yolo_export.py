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


#: 切片边长与重叠（像素）。
#:
#: **为什么必须切片**：实测整图训练时框的中位尺寸在 1024px 下只有
#: **3.7 × 4.8 像素**、P10 是 1.7 × 0.9 像素。YOLO 最小可检测目标约
#: 8~10 像素，而这些框在 P3 特征层（1/8 下采样）上只剩 0.46 像素，
#: **根本训不出来**。切片后构件在块内的相对尺寸放大约 7 倍。
TILE_PX = 640
TILE_OVERLAP_PX = 64

#: 框中心必须落在块内，且块内保留面积不低于此比例——
#: 跨在切线上只剩一丝的框是噪声。
MIN_TILE_BOX_KEEP = 0.5


def tile_grid(width: int, height: int, tile: int = TILE_PX,
              overlap: int = TILE_OVERLAP_PX) -> list[tuple]:
    """整图 → 切片窗口列表 `(x0, y0, x1, y1)`。

    **重叠是必须的**：构件跨在切线上时两块各留一部分，
    不重叠就两边都不完整。
    """
    step = max(1, tile - overlap)
    out = []
    y = 0
    while y < height:
        x = 0
        while x < width:
            out.append((x, y, min(x + tile, width), min(y + tile, height)))
            if x + tile >= width:
                break
            x += step
        if y + tile >= height:
            break
        y += step
    return out


def boxes_in_tile(boxes: list, page_w: int, page_h: int,
                  tile: tuple) -> list[tuple]:
    """整图归一化框 → 该切片内的归一化框。

    中心在块外的丢弃；跨块的裁到块内，保留面积不足一半的也丢弃——
    **宁可少一个样本，不要一个残框**。
    """
    x0, y0, x1, y1 = tile
    tw, th = x1 - x0, y1 - y0
    if tw <= 0 or th <= 0:
        return []
    out = []
    for cls, cx, cy, w, h in boxes or []:
        px, py = cx * page_w, cy * page_h
        pw, ph = w * page_w, h * page_h
        if not (x0 <= px <= x1 and y0 <= py <= y1):
            continue
        bx0, by0 = max(px - pw / 2, x0), max(py - ph / 2, y0)
        bx1, by1 = min(px + pw / 2, x1), min(py + ph / 2, y1)
        if bx1 <= bx0 or by1 <= by0:
            continue
        if (bx1 - bx0) * (by1 - by0) < MIN_TILE_BOX_KEEP * max(pw * ph, 1e-9):
            continue
        out.append((cls, ((bx0 + bx1) / 2 - x0) / tw,
                    ((by0 + by1) / 2 - y0) / th,
                    (bx1 - bx0) / tw, (by1 - by0) / th))
    return out


#: 判为「同一根柱的重复框」的 IoU 阈值。
#: 实测取 0.1 时 N8 的 24 框合并成 6，与 GPT 及人工核对完全一致。
DUPLICATE_IOU = 0.1


def _box_iou(a: tuple, b: tuple) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = ((a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter)
    return inter / union if union > 0 else 0.0


CONTAINED_FRACTION = 0.9      # 小框有九成面积落在大框内即视为同一构件


def _mostly_contained(a: tuple, b: tuple,
                      fraction: float = CONTAINED_FRACTION) -> bool:
    """小框是否实质落在大框内——**IoU 去重的盲区**。

    IoU = 交/并，小框套在大框内时 IoU ≈ 小/大，可以远低于阈值而逃脱。
    实测柱框有 14%（大歌剧院）~20%（轨道交通）被更大的框实质包含，
    最严重一图 **95%**（381/400）；这正是「53 个框零重叠却只有 13 根柱」
    的来源——它们不是不重叠，是重叠得**太彻底**以致 IoU 判不出来。
    """
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    small = min(area_a, area_b)
    if small <= 0:
        return False
    overlap = (max(0.0, min(ax1, bx1) - max(ax0, bx0))
               * max(0.0, min(ay1, by1) - max(ay0, by0)))
    return overlap >= small * fraction


def merge_duplicate_boxes(boxes: list | None,
                          iou_threshold: float = DUPLICATE_IOU) -> list:
    """合并重叠的同类框——**规则引擎对同一根柱吐多个框**。

    实测（三方独立核对）：N8 切片 24 个原始框合并后 **6 个**，
    而 GPT 数 6、人工数也是 6。6 块抽样里 3 块去重后与 GPT 计数
    完全对上（比值 0.88~1.10）。

    重复框对训练有害：教模型输出重复，并让那些区域的损失被重复计算。
    合并取**并集**而非留其一——留其一会丢掉柱的真实轮廓范围。
    **不同类别永不合并**（柱站在板上是常态）。
    """
    items = list(boxes or [])
    corners = [(cls, cx - w/2, cy - h/2, cx + w/2, cy + h/2)
               for cls, cx, cy, w, h in items]
    n = len(corners)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if corners[i][0] != corners[j][0]:
                continue                      # 不同类别不合并
            if (_box_iou(corners[i][1:], corners[j][1:]) > iou_threshold
                    or _mostly_contained(corners[i][1:], corners[j][1:])):
                a, b = find(i), find(j)
                if a != b:
                    parent[a] = b

    groups: dict = {}
    for index, corner in enumerate(corners):
        groups.setdefault(find(index), []).append(corner)
    out = []
    for members in groups.values():
        cls = members[0][0]
        x0 = min(m[1] for m in members); y0 = min(m[2] for m in members)
        x1 = max(m[3] for m in members); y1 = max(m[4] for m in members)
        out.append((cls, (x0+x1)/2, (y0+y1)/2, x1-x0, y1-y0))
    return out
