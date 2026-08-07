"""把轴距序列的匹配结果翻译成**轴号对应关系**（J1 收尾）。

匹配给出 ``GapMatch(start_index, spans)``：局部第 i 段吃掉了 ``spans[i]`` 个锚距。
于是局部第 i 条**轴线**对应锚图第 ``start_index + sum(spans[:i])`` 条。

拿到轴号后，局部图交点的世界坐标 = 锚图同名轴号对的世界坐标 ——
这是 ``axis_intersections`` 的写入依据，也是 `placements_for_project` 的输入。

**必须双向**：一个交点要 x、y 两个轴号才能确定。实测 143 张匹配成功的图里
只有 **12 张双向**、131 张单向 —— 单向拿不到世界坐标。
"""
from __future__ import annotations

from typing import Sequence

from services.axis_sequence_match import GapMatch


def anchor_labels_for_local_axes(
    local_axis_count: int, match: GapMatch | None,
    anchor_labels: Sequence[str],
) -> list[str | None]:
    """局部图每条轴线对应的**锚图轴号**；越界或锚图无号处为 ``None``。

    轴线数必须比段数多 1（k 段轴距对应 k+1 条轴线）。对不上说明调用方
    传错了数据 —— 返回空列表而不是猜一个对应关系。
    """
    if match is None or local_axis_count <= 0:
        return []
    spans = list(match.spans or [])
    if local_axis_count != len(spans) + 1:
        return []

    out: list[str | None] = []
    cursor = int(match.start_index)
    for index in range(local_axis_count):
        if 0 <= cursor < len(anchor_labels):
            label = str(anchor_labels[cursor]).strip()
            out.append(label or None)      # 锚图那条本身没轴号 ⇒ 传不出身份
        else:
            out.append(None)               # 越界不编号，宁缺勿错
        if index < len(spans):
            cursor += int(spans[index])
    return out
