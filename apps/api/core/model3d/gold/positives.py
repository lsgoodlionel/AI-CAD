"""正对照格的登记表 —— 已核验为真的格子，按「图纸 + 页面坐标」记着。

**为什么不存图片**：裁图是客户图纸的局部，版权产物不进仓库。存
「图纸 id + 标记框 + 裁框」（都是页面点），出批次时按原样重渲染即可 ——
不必重跑识别，因此与识别代码的任何改动都无关，对照格永远是同一张图。

一行一格，制表符分隔，`#` 开头是注释：

    drawing_id\tmark_pt\tcrop_pt\tsource\tnote\tclaim

`mark_pt` / `crop_pt` 是 `x0,y0,x1,y1` 四个页面点坐标（与 manifest 同口径）。
`source` 记这格从哪来（如 `col4:L9AK`），`note` 记核验时看到了什么，
`claim` 是「系统读数」类要一并画出的那个值（标高 / 轴号 / 交点），构件类留空。
"""
from __future__ import annotations

from dataclasses import dataclass

HEADER = "drawing_id\tmark_pt\tcrop_pt\tsource\tnote\tclaim"

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class PositiveCell:
    """一格正对照。"""
    drawing_id: str
    mark_pt: Box
    crop_pt: Box
    source: str = ""
    note: str = ""
    #: 「系统读数」类的读数（画在格子右上角）；构件类为空
    claim: str = ""


def _box(text: str) -> Box:
    parts = [float(p) for p in text.strip().strip("()[]").split(",")]
    if len(parts) != 4:
        raise ValueError(f"坐标要四个数，得到 {text!r}")
    return (parts[0], parts[1], parts[2], parts[3])


def parse_positives(text: str) -> list[PositiveCell]:
    """解析登记表。格式不对就抛 —— 对照格出错等于仪器失准，不静默跳过。"""
    cells: list[PositiveCell] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\n")
        # 表头按首列名认，不按整行比对 —— 加一列（`claim`）时旧表不该变成数据行
        if not line.strip() or line.lstrip().startswith("#") \
                or line.split("\t", 1)[0].strip() == "drawing_id":
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            raise ValueError(f"第 {lineno} 行少于 3 列：{line!r}")
        did, mark, crop = parts[0].strip(), parts[1], parts[2]
        cells.append(PositiveCell(did, _box(mark), _box(crop),
                                  parts[3].strip() if len(parts) > 3 else "",
                                  parts[4].strip() if len(parts) > 4 else "",
                                  parts[5].strip() if len(parts) > 5 else ""))
    return cells


def format_positives(cells: list[PositiveCell]) -> str:
    """登记表文本（含表头）。"""
    def fmt(b: Box) -> str:
        return ",".join(f"{v:.2f}" for v in b)
    rows = [HEADER] + [f"{c.drawing_id}\t{fmt(c.mark_pt)}\t{fmt(c.crop_pt)}"
                       f"\t{c.source}\t{c.note}\t{c.claim}" for c in cells]
    return "\n".join(rows) + "\n"
