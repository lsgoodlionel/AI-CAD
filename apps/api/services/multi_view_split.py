"""一图多视图（分幅）识别 —— 它不是 GB/T 50001 §8.0.5 的分区。

**实测错误**：`A-20-02A 南立面图` 一张纸画了两幅立面（南立面图一/二），
系统把第二幅当成独立分区、按 §8.0.5 规则**从 1 重新编号**：

| 分区 | 系统推导 | 图纸真值 |
|---|---|---|
| 0 | `1`~`13` | `1-1`~`1-13` ✅ |
| 1 | **`1`~`12`** | **`1-13`~`1-24`** ❌ 整段错 |

两幅在 `1-13` 处搭接重复一根轴线，是**同一分区的连续序列**。

**判别指纹（实测）**：

| 图 | 各区的数字/字母轴线数 | 判定 |
|---|---|---|
| A-01-02A 正交轴网定位图 | 24/**15**、16/**15**、15/**14** | 真分区（**双向**）|
| A-20-02A 南立面图 | 13/**0**、12/**0** | 分幅（**单向**）|
| A-30-07A 剖面图 | 7/**0**、6/**0** | 分幅（**单向**）|

§8.0.5 的分区是**平面上**的分区，两个方向都要标注轴线（§8.0.2
「编号注写在平面图下方及左侧」）。而立面/剖面是投影图，只有一个方向的
轴线投影 —— **单向就是分幅的指纹**。

**串号是建议，不是结论**：`renumber_split_views` 的产出只存进
`split_view_numbering` 供人核，**不改写轴号本身**。

原因是一个实测限制：`A-20-02A` 是同一立面分两幅（**该串号**，
实测串出 `1~13` + `13~24`，与图纸真值 `1-1`~`1-13`、`1-13`~`1-24` 吻合），
而 `A-30-07A` 一页画了**四个独立剖面**（7-7/8-8/9-9/10-10，各有各的轴号，
**不该串**）。两者在轴网几何上形态相同（都是单向多区），
要分开得读各幅图名——那是 OCR 的事，不是轴网识别能定的。

**搭接根数同样是假设**：制图惯例是分幅处重复一根轴线以便对位，
但这不是国标强制。假设值记进 `overlap_assumed`，让人能核。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

#: 识别到分幅时给出的警告。要说清**为什么**，不能只说「疑似」。
SPLIT_VIEW_WARNING = (
    "检出多个仅单向标注轴号的分区 —— 这是**一图多视图**"
    "（如「南立面图(一)/(二)」、一页四个剖面），"
    "不是 GB/T 50001 §8.0.5 的分区编号:"
    "§8.0.5 的分区在平面上两个方向都标轴号，而投影图只有一个方向。"
    "已给出跨幅连续编号的**建议方案**（`split_view_numbering`），"
    "但**未改写轴号本身**,需人工确认后采用 —— "
    "因为「同一视图分幅」（南立面一/二，**该串号**）与"
    "「多个独立视图」（一页四个剖面，各有各的轴号，**不该串**）"
    "在轴网几何上形态相同,要靠各幅图名才能分开。"
)


def _is_one_directional(zone: Mapping[str, Any]) -> bool:
    numeric = int(zone.get("numeric_axes") or 0)
    alpha = int(zone.get("alpha_axes") or 0)
    return (numeric > 0) != (alpha > 0)      # 恰有一个方向有轴号


def is_split_view(zones: Sequence[Mapping[str, Any]]) -> bool:
    """这些「分区」是否其实是**一图多视图的分幅**。

    判据：**≥2 个分区，且每个都只有单向轴号**。

    一个分区谈不上分幅；只要有一个是双向的，就说明确实存在平面分区，
    不能整体按分幅处理（**宁可不改，也不能把真分区串成一条**）。
    """
    if len(zones) < 2:
        return False
    return all(_is_one_directional(z) for z in zones)


def _extent(zone: Mapping[str, Any]) -> tuple[float, float, float, float]:
    ext = zone.get("extent") or [0.0, 0.0, 0.0, 0.0]
    return tuple(float(v) for v in (list(ext) + [0.0] * 4)[:4])  # type: ignore[return-value]


def order_split_zones(
    zones: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """按图面阅读顺序排列各幅：**自上而下、同行自左至右**。

    PDF 坐标 y 向下增大，所以 y 小的在图面上方、排在前。
    同行左右并列时按 x（§8.0.3 横向编号从左至右的同一方向感）。
    """
    def key(zone: Mapping[str, Any]) -> tuple[float, float]:
        x0, y0, _x1, _y1 = _extent(zone)
        return (y0, x0)
    return sorted(zones, key=key)


def renumber_split_views(
    zones: Sequence[Mapping[str, Any]], *, overlap: int = 1,
) -> list[dict[str, Any]]:
    """把各幅的轴号串成一条连续序列。

    `overlap` 是**相邻两幅搭接重复的轴线根数**（制图惯例为 1）。
    它是**假设**不是识别结果，会记进每条结果的 `overlap_assumed`。

    非分幅（真分区）时返回空列表 —— §8.0.5 规定各分区**各自**从 1 编号，
    串号会毁掉正确结果。
    """
    if not is_split_view(zones):
        return []

    out: list[dict[str, Any]] = []
    cursor = 1
    for position, zone in enumerate(order_split_zones(zones)):
        count = int(zone.get("numeric_axes") or 0) or int(zone.get("alpha_axes") or 0)
        if count <= 0:
            continue
        start = cursor
        end = start + count - 1
        out.append({
            "index": zone.get("index"),
            "position": position,
            "start": start,
            "end": end,
            "count": count,
            # 第一幅之前没有搭接
            "overlap_assumed": overlap if position > 0 else 0,
        })
        # 下一幅从「本幅末号 − 搭接根数 + 1」开始
        cursor = end - overlap + 1
    return out
