"""用世界锚点钉住轴网帧（K-3 第二级）。

**实测**：`axis_intersections` 里 5401 个交点**全部带世界坐标**，
覆盖大歌剧院 76 张图。含锚点的帧 36 个，能钉住 **445 张图（32%）**——
比纯靠帧间共有轴号的 12% 高出一倍多。

锚点是**强证据**：轴号交点 → 实测世界坐标（Phase I 实测 RMSE 5.7 毫米）。
帧间共有轴号是**弱证据**：两个帧碰巧都有轴号「1」不代表它们是同一片轴网。
所以两级配准——先用锚点钉，再让其余帧向已钉住的帧对齐。
"""
from __future__ import annotations


def _median(values: list) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def solve_frame_world_offset(axes: dict | None,
                             anchors: list | None) -> dict | None:
    """帧 + 世界锚点 → 帧到世界的平移量。

    帧内的相对关系已由 K-1 定死（残差毫米级），所以**一个锚点就够定平移**。
    多个锚点取中位——一个标错不会带偏整帧。

    锚点的轴号不在本帧里就用不上，**不能拿它硬凑**；
    世界坐标为空是「没测过」不是「在原点」。
    只有一个方向对得上时，**那个方向照钉，另一个如实返回 None**——
    不要因为一半缺失就整个放弃，也不要给缺的那半编一个 0。
    """
    grid = axes or {}
    x_axes, y_axes = grid.get("x") or {}, grid.get("y") or {}
    dx, dy = [], []
    for anchor in anchors or []:
        data = dict(anchor)
        lx, ly = str(data.get("label_x") or ""), str(data.get("label_y") or "")
        wx, wy = data.get("world_x"), data.get("world_y")
        if lx in x_axes and wx is not None:
            dx.append(float(wx) - float(x_axes[lx]))
        if ly in y_axes and wy is not None:
            dy.append(float(wy) - float(y_axes[ly]))
    if not dx and not dy:
        return None
    return {"x": _median(dx) if dx else None,
            "y": _median(dy) if dy else None}
