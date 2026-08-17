"""轴线法向偏移 —— **全系统唯一实现**。

给定方向角 θ,方向向量是 `(cosθ, sinθ)`,其法向必须是 **`(-sinθ, cosθ)`**
(点积为 0)。点 P 到「过原点、方向 θ」的直线的**带符号法向距离**就是
`P · (-sinθ, cosθ)`。同一条直线上的所有点偏移相同,因此它是把碎段/圈心
归并成「同一条轴线」的判据。

**为什么必须只有一份实现**:这里曾用过 `(sinθ, cosθ)`,它与方向向量的点积是
`sin2θ`——**只在 0°/90° 为零**。于是斜向轴线上同一条线的碎段被算出完全不同的
偏移,永远聚不成一条;而正交方向侥幸正确,把问题掩盖了很久(实测导致 42°/132°
的整套旋转分区被判成「斜撑构件」)。

该公式此前在三处各写了一遍(`vector_axis_extractor`、`axis_label_circle`、
`services/axis_geometry`),其中两处逐字重复。修那个符号 bug 时必须同时改三处,
以后只改一处就会静默劣化——所以收敛到本模块。

**偏移带符号,只在同一方向内可比**;跨方向比较没有意义。
"""
from __future__ import annotations

import math


def normal_offset(x: float, y: float, angle_deg: float) -> float:
    """点 (x, y) 到过原点、方向 angle_deg 的直线的带符号法向距离。"""
    rad = math.radians(angle_deg)
    return -x * math.sin(rad) + y * math.cos(rad)


def normal_vector(angle_deg: float) -> tuple[float, float]:
    """方向角 → 单位法向 `(-sinθ, cosθ)`。"""
    rad = math.radians(angle_deg)
    return -math.sin(rad), math.cos(rad)


def along_offset(x: float, y: float, angle_deg: float) -> float:
    """点 (x, y) 沿方向 angle_deg 的一维坐标(与法向偏移正交的那一维)。"""
    rad = math.radians(angle_deg)
    return x * math.cos(rad) + y * math.sin(rad)
