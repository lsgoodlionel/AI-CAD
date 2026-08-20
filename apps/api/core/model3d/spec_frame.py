"""说明块的矢量边框。

**为什么用它**：图纸上的说明区域**常有矢量边框**——实测四张说明字数
最多的图，每个说明块都被 2~4 个矢量矩形包住。

这是**图纸独有的结构信号**：文档版面分析模型看不见它
（§8.36 实测 `pp_layout_cdla` 与 `doclayout_docstructbench` 两个家族
都把整张图纸判成一个 `figure`，裁到纯文字区域仍是），
而矢量数据里它就明明白白摆着。

现有的块边界靠两条启发式：遇到下一个标题、或垂直间距超过 60pt。
边框比这两条都准——**它是制图者画出来的真实边界**。
"""
from __future__ import annotations

#: 能当说明框的最小边长（pt）。字号大小的小方框（表格单元、符号）
#: 不是说明框，用它定边界会把一个块碎成几十片。
MIN_FRAME_SIDE_PT = 40.0


def _sides(frame: dict) -> tuple[float, float, float, float] | None:
    try:
        x0, y0 = float(frame["x0"]), float(frame["y0"])
        x1, y1 = float(frame["x1"]), float(frame["y1"])
    except (KeyError, TypeError, ValueError):
        return None
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def enclosing_frames(frames: list[dict] | None,
                     x: float, y: float) -> list[dict]:
    """包住 (x, y) 的**所有**合格矩形，按面积从小到大。

    **为什么不能只取最小的**：实测图上有 67 个矢量框，最小的那个往往是
    标题附近的表格单元而非说明框——据它定界会把 1390 字的正文切到 46 字。
    调用方要在候选里挑「能容纳最长连续正文」的那个。
    """
    out = []
    for frame in frames or []:
        sides = _sides(frame if isinstance(frame, dict) else {})
        if sides is None:
            continue
        x0, y0, x1, y1 = sides
        if (x1 - x0) < MIN_FRAME_SIDE_PT or (y1 - y0) < MIN_FRAME_SIDE_PT:
            continue
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            continue
        out.append(({"x0": x0, "y0": y0, "x1": x1, "y1": y1},
                    (x1 - x0) * (y1 - y0)))
    return [f for f, _ in sorted(out, key=lambda t: t[1])]


def enclosing_frame(frames: list[dict] | None,
                    x: float, y: float) -> dict | None:
    """包住 (x, y) 的**最小**矩形；没有就返回 None。

    一个点会被多个矩形包住（图框、图签区、说明框），
    要的是最小的那个——否则边界会宽到把半张图圈进来。

    零宽/零高的「矩形」其实是线段，字号大小的小方框是表格单元或符号，
    两者都不能当说明框。**框不住就说框不住**，不给一个「最近的」凑数。
    """
    best = None
    best_area = None
    for frame in frames or []:
        sides = _sides(frame if isinstance(frame, dict) else {})
        if sides is None:
            continue
        x0, y0, x1, y1 = sides
        if (x1 - x0) < MIN_FRAME_SIDE_PT or (y1 - y0) < MIN_FRAME_SIDE_PT:
            continue
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            continue
        area = (x1 - x0) * (y1 - y0)
        if best_area is None or area < best_area:
            best, best_area = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}, area
    return best


def contains(frame: dict | None, x: float, y: float) -> bool:
    """点是否在框内。没有框时视为「不限制」——
    没有边框数据的图要退回原有启发式，不能因为新能力就要求人人都有它。"""
    if frame is None:
        return True
    sides = _sides(frame)
    if sides is None:
        return True
    x0, y0, x1, y1 = sides
    return x0 <= x <= x1 and y0 <= y <= y1
