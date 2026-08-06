"""楼层名 ↔ 标高 配对(纯函数,离线可测)。

立面图/剖面图的标高链上，楼层名与标高数字**写在同一水平行**上：

```
大歌剧厅屋顶层  ▽ 45.500
6F（设备层）    ▽ 36.800
5F（设备层）    ▽ 26.900
```

档案层（`drawing_extracted_info`）已经存了每条文本的 `location_json.bbox`，
所以配对只需几何邻近，不需要再读一次图。

**国标依据**：GB/T 50001 §11.8.3「标高数字应注写在标高符号的上侧或下侧」
——标高数字与其名称处在同一水平线上；§11.8.4 标高以米为单位、小数点后三位。

**为什么这件事重要**：模型 v31 的 13 层里 10 层标高是
`DEFAULT_STORY_HEIGHT_M = 4.5` 硬推出来的，与图纸实测最大差 **11.9 米**
（F6 设备层：模型 24.9，图纸 36.800）。配对拿到的是**图纸上写着的**楼层标高表。
"""
from __future__ import annotations

import re

#: 同一行的判据：两个 bbox 的**垂直中心**之差。
#: 实测 A-20-02A：`36.800` 中心 y=243.36，`6F（设备层` 中心 y=246.60，差 3.2pt。
#: 取 8pt —— 略大于一个字高，容得下基线差异，又不会跨到相邻行
#: （实测标高链行距 60~120pt，留有充足余量）。
SAME_ROW_TOLERANCE_PT = 8.0

#: 水平间隙上限。楼层名紧挨着标高（实测间隙 3.6pt）；
#: 图例区那个同名的 `5F（设备层）` 在 x=1883，离标高链 1700pt 远，必须挡掉。
MAX_HORIZONTAL_GAP_PT = 120.0

#: §11.8.4 标高以米为单位、注写到小数点后第三位；§11.8.5 零点写 `±0.000`。
_RE_ELEVATION = re.compile(r"^[±+\-]?\d{1,3}\.\d{3}$")

#: 主标高链的成列判据：同一条链上的标高，x 中心互差不超过此值。
#: 实测 A-20-02A 标高链 x∈[126, 140]，而右侧图例区在 x≈1883。
CHAIN_COLUMN_TOLERANCE_PT = 40.0

#: 成链的最少成员数。两个孤立标高不成链——否则图例区随便两个数字
#: 也会被当成主链。立面/剖面的标高链实测都有 5~13 个成员。
MIN_CHAIN_MEMBERS = 3


def _bbox(item: dict) -> tuple[float, float, float, float] | None:
    box = (item.get("location_json") or {}).get("bbox")
    if not box or len(box) < 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in box[:4])
    except (TypeError, ValueError):
        return None
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def parse_elevation_m(text: str) -> float | None:
    """`±0.000`→0.0，`36.800`→36.8。不合 §11.8.4 形式的返回 None。"""
    raw = str(text or "").strip()
    if not _RE_ELEVATION.match(raw):
        return None
    try:
        return float(raw.replace("±", ""))
    except ValueError:
        return None


def _horizontal_gap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """两个 bbox 的水平间隙；重叠则为 0。左右两侧都算，不假定楼层名在哪边。"""
    if a[2] < b[0]:
        return b[0] - a[2]
    if b[2] < a[0]:
        return a[0] - b[2]
    return 0.0


def _parsed_elevations(items: list[dict]) -> list[tuple[tuple[float, ...], float]]:
    out = []
    for item in items:
        box = _bbox(item)
        value = parse_elevation_m(item.get("content", ""))
        if box is not None and value is not None:
            out.append((box, value))
    return out


def main_elevation_chain(
    elevation_items: list[dict], *,
    column_tolerance_pt: float = CHAIN_COLUMN_TOLERANCE_PT,
    min_members: int = MIN_CHAIN_MEMBERS,
) -> list[dict]:
    """找出**主标高链**——立面/剖面图上竖向排成一列的那组标高。

    **为什么需要**：A-20-02A 右侧图例/索引区也写着 `5F（设备层）`
    `6F（设备层）`，旁边恰好有别的标高数字，只按「同一行 + 水平相邻」配对
    会配出 `5F（设备层）→22.000`、`6F（设备层）→29.800` 两个错值
    （真值 26.900 / 36.800）。**错标高比没标高更糟**——它会让整层构件
    落到错误高度。

    判据：按 x 中心聚类，取成员最多的那一列；不足 `min_members` 则判为
    「没有主链」，返回空——宁可不给，也不给错的。

    返回 ``[{"elevation_m", "bbox"}]``，按标高升序。
    """
    parsed = _parsed_elevations(elevation_items)
    if len(parsed) < min_members:
        return []

    columns: list[list[tuple[tuple[float, ...], float]]] = []
    for box, value in sorted(parsed, key=lambda p: (p[0][0] + p[0][2]) * 0.5):
        cx = (box[0] + box[2]) * 0.5
        for column in columns:
            last = column[-1][0]
            if abs(cx - (last[0] + last[2]) * 0.5) <= column_tolerance_pt:
                column.append((box, value))
                break
        else:
            columns.append([(box, value)])

    best = max(columns, key=len)
    if len(best) < min_members:
        return []
    return sorted(
        ({"elevation_m": round(v, 3), "bbox": list(b)} for b, v in best),
        key=lambda c: c["elevation_m"])


def pair_levels_with_elevations(
    elevation_items: list[dict], level_items: list[dict], *,
    row_tolerance_pt: float = SAME_ROW_TOLERANCE_PT,
    max_gap_pt: float = MAX_HORIZONTAL_GAP_PT,
    chain_only: bool = False,
) -> list[dict]:
    """把同一行、且水平相邻的（楼层名, 标高）配成对。

    每项形如 ``{"content": str, "location_json": {"bbox": [x0,y0,x1,y1]}}``
    ——即 `drawing_extracted_info` 的行结构。

    **一对一**：按距离从近到远贪心配对，配过的两边都不再参与。
    配不上的**如实丢弃，绝不硬凑**——宁可少给一层标高，
    也不能给一个错的（错标高会让整层构件放到错误高度）。

    返回按标高升序排列的 ``[{"level_name", "elevation_m", "distance_pt"}]``。
    """
    if chain_only:
        # 只在主标高链上配对（见 `main_elevation_chain`）——挡掉图例区同名楼层名
        elevations = [(tuple(c["bbox"]), c["elevation_m"])
                      for c in main_elevation_chain(elevation_items)]
    else:
        elevations = _parsed_elevations(elevation_items)

    levels = []
    for item in level_items:
        box = _bbox(item)
        name = str(item.get("content") or "").strip()
        if box is not None and name:
            levels.append((box, name))

    candidates = []
    for ei, (ebox, value) in enumerate(elevations):
        ecy = (ebox[1] + ebox[3]) * 0.5
        for li, (lbox, name) in enumerate(levels):
            lcy = (lbox[1] + lbox[3]) * 0.5
            row_delta = abs(ecy - lcy)
            if row_delta > row_tolerance_pt:
                continue
            gap = _horizontal_gap(ebox, lbox)
            if gap > max_gap_pt:
                continue
            candidates.append((gap + row_delta, ei, li, name, value))

    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    used_elev: set[int] = set()
    used_level: set[int] = set()
    paired = []
    for distance, ei, li, name, value in candidates:
        if ei in used_elev or li in used_level:
            continue
        used_elev.add(ei)
        used_level.add(li)
        paired.append({"level_name": name, "elevation_m": round(value, 3),
                       "distance_pt": round(distance, 2)})

    return sorted(paired, key=lambda p: p["elevation_m"])
