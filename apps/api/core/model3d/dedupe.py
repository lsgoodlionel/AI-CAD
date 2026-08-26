"""构件去重：识别器对同一个构件会吐多个框。

**为什么必须在识别器里做**：`services/model_qto.py` 是
`for column in columns` 逐个累加体积的，同一根柱出现 N 次，
混凝土量与模板面积就乘 N —— 而算量喂给创效提案。

**为什么 IoU 不够**：IoU = 交/并，小框套在大框里时 IoU ≈ 小/大，
可以远低于阈值而逃脱。实测柱框有 14%~20% 被更大的框实质包含，
最严重一图 95%（381/400）。判据必须同时看 IoU 与包含。
"""

#: 小框有九成面积落在大框内即视为同一构件
CONTAINED_FRACTION = 0.9
#: 交并比超过此值视为同一构件
DUPLICATE_IOU = 0.1


def _area(box: tuple) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _overlap(a: tuple, b: tuple) -> float:
    return (max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
            * max(0.0, min(a[3], b[3]) - max(a[1], b[1])))


def is_same_element(a: tuple, b: tuple,
                    contained_fraction: float = CONTAINED_FRACTION,
                    iou_threshold: float = DUPLICATE_IOU) -> bool:
    """两个包围盒是否指向同一个构件（IoU 高 **或** 一个实质落在另一个内）。"""
    area_a, area_b = _area(a), _area(b)
    if area_a <= 0 or area_b <= 0:
        return False
    overlap = _overlap(a, b)
    if overlap <= 0:
        return False
    if overlap / (area_a + area_b - overlap) > iou_threshold:
        return True
    return overlap >= min(area_a, area_b) * contained_fraction


def _bbox(outline) -> tuple | None:
    points = [p for p in (outline or []) if p is not None and len(p) >= 2]
    if len(points) < 3:
        return None
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def merge_overlapping(elements: list | None, **thresholds) -> list:
    """合并指向同一构件的重复项，**保留面积最大的那个真实轮廓**。

    保留真实轮廓而非合成包围盒：算量吃的是轮廓，把八边形柱换成
    外接矩形会让面积抬高约 27%。

    轮廓残缺（点数 < 3）的构件原样保留 —— 去重不该顺手丢数据。
    """
    items = list(elements or [])
    boxes = [_bbox(e.get("outline") if isinstance(e, dict) else None)
             for e in items]
    usable = [i for i, b in enumerate(boxes) if b is not None]

    parent = {i: i for i in usable}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # **按 x 扫描**而非两两比较：单图柱数上限 2000，朴素 O(n²) 是 400 万次，
    # 整机 2000+ 张图会把建模拖垮。构件在图上是散开的，扫描后近似线性。
    order = sorted(usable, key=lambda i: boxes[i][0])
    for pos, i in enumerate(order):
        right = boxes[i][2]
        for j in order[pos + 1:]:
            if boxes[j][0] > right:
                break                      # 之后的 x 只会更大，不可能相交
            if is_same_element(boxes[i], boxes[j], **thresholds):
                a, b = find(i), find(j)
                if a != b:
                    parent[b] = a

    groups: dict[int, list[int]] = {}
    for i in usable:
        groups.setdefault(find(i), []).append(i)

    # 每组留面积最大的一个；索引升序输出，结果不依赖输入顺序
    survivors = {max(members, key=lambda i: (_area(boxes[i]), -i))
                 for members in groups.values()}
    return [e for i, e in enumerate(items)
            if boxes[i] is None or i in survivors]
