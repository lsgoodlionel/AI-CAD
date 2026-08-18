"""哪些图的轴线**不参与装配** —— 只排除不删除。纯函数。

**实测背景**(第二工程,轨道交通):5 张详图产出 7~35 条轴线,
全部未被「轴距过密」判据拦下。`钢立柱及立柱桩详图` 的 21 个「圈」
显然是桩位,但**详图比例尺比平面图大一个量级**(1:20 vs 1:100),
同样的图上距离换算出的米数完全不同 —— 绝对尺度判据在详图上失效。

**更根本的判据是国标本身**:GB/T 50001 §8 规定定位轴线用于
**平面定位**,而详图表达的是局部构造,不表达平面定位。
所以判据不该是「轴距多少」,而该是「这张图表达平面定位吗」。

处置与 `suspect_symbol_field` 一致:**只排除不删除** ——
轴线照常留档可查,只是不进 3D 场景与世界锚点。
"""
from __future__ import annotations

from typing import Any, Mapping


def excluded_from_assembly(drawing: Mapping[str, Any] | None) -> str | None:
    """该图的轴线是否应排除出装配 → 原因;不排除返回 None。

    **判不出就不排除**:宁可多一张待人审的图，不可少一张真轴网。
    """
    if not drawing:
        return None
    from services.drawing_role import ROLE_DETAIL, classify_role

    try:
        role = classify_role(dict(drawing)).role
    except Exception:      # noqa: BLE001 — 判不出即不排除
        return None
    if role == ROLE_DETAIL:
        return ("详图表达局部构造，不表达平面定位（GB/T 50001 §8 定位轴线"
                "用于平面定位）——轴线留档可查，但不进 3D 场景与世界锚点")
    return None
