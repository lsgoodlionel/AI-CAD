"""单体归属兜底:区分「本就没有单体」与「该有却没识别出来」。

**为什么重要**:1866 / 2309 = **80.8%** 图纸 `semantic_unassigned`，
而标高、构件归属全挂在单体上——同一个 `F3` 在南区是 10.300、北区是 10.800，
**单体没定，标高就定不了**。

**但 80.8% 这个数字本身是误导的。** 实测拆开:

| 类 | 数 | 说明 |
|---|---:|---|
| 非几何（目录/说明/表/系统图） | 247 | **本就没有单体** |
| 详图（大样/楼梯/卫生间/节点） | 351 | **本就没有单体** |
| 围护基坑（施工阶段，非主体） | 20 | **本就没有单体** |
| **有楼层却缺单体** | **778** | ← 真损失 |
| 其他 | 470 | — |

**618 张本就不该有单体归属**，把它们算进「未分配」等于虚报损失，
还会让人去优化一个不存在的问题。

**四种状态**:

* `assigned` —— 从图名读出了单体
* `defaulted` —— 有楼层但无单体，**降级挂默认单体**（可用，但标记为降级）
* `not_applicable` —— 本就没有单体归属，**不计入损失**
* `unresolved` —— 既无单体又无楼层，如实标注，**不硬挂**

判据复用 `services/drawing_role.py`——非几何与详图由**国标术语**判出，
不依赖任何工程的图号体系。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from services.drawing_role import (
    ROLE_DETAIL, ROLE_NON_GEOMETRIC, classify_role,
)

UNIT_ASSIGNED = "assigned"
UNIT_DEFAULTED = "defaulted"
UNIT_NOT_APPLICABLE = "not_applicable"
UNIT_UNRESOLVED = "unresolved"

#: 无单体线索但有楼层时挂靠的默认单体。与 `model_story` 的 `main` 一致。
DEFAULT_UNIT_KEY = "main"

#: 方位单体。GB/T 50001 §8.0.5「子项号-轴线号」的子项在中文工程里
#: 最常写成方位（南区/北区/东区/西区/中区）。
_DIRECTIONAL = (
    (re.compile(r"[东][区侧段]"), "east"),
    (re.compile(r"[南][区侧段]"), "south"),
    (re.compile(r"[西][区侧段]"), "west"),
    (re.compile(r"[北][区侧段]"), "north"),
    (re.compile(r"[中][区段]"), "center"),
)

#: 围护/基坑/施工阶段图——**不属于主体单体**。
#: 它们的标高是挡土、支护、深坑的竖向标高，纳入楼层体系等于拿基坑标高
#: 冒充楼层标高（这条经验已写在 `section_z_recovery` 里）。
#: 术语取自实测未判定名单:`1区首道支撑结合栈桥`、`第二道支撑平面布置`、
#: `钻孔灌注桩说明`、`基础底板换撑平面布置` —— 都是施工阶段的临时结构。
_EXCAVATION = re.compile(
    r"围护|基坑|支护|挡土|工况|连通道|联通道|地铁|地质|降水|土方|环境总平"
    r"|支撑|栈桥|换撑|灌注桩|立柱桩|深坑|冠梁|止水|降承压")

#: 楼层线索。有楼层就说明这张图画的是主体的某一层，值得挂默认单体。
_FLOOR_HINT = re.compile(
    r"地下[一二三四五六七八九十\d]+层|[一二三四五六七八九十\d]+层|首层"
    r"|屋[顶面]|夹层|台仓|B\d{1,2}\b|\d{1,2}F\b|F\d{1,2}\b|RF\b")


@dataclass(frozen=True)
class UnitAssignment:
    """单体归属结论。`reason` 用于事后分辨识别值与兜底值。"""
    status: str
    unit_key: str | None
    reason: str


def classify_unit_assignment(drawing: Mapping[str, object]) -> UnitAssignment:
    """判定一张图的单体归属状态。"""
    title = " ".join(str(drawing.get(key) or "")
                     for key in ("title", "drawing_no", "filename"))

    # ① 有明确单体线索——优先，压过一切兜底
    for pattern, unit in _DIRECTIONAL:
        if pattern.search(title):
            return UnitAssignment(UNIT_ASSIGNED, unit,
                                  f"图名含方位单体:{pattern.pattern[:8]}")

    # ② 围护/基坑/施工阶段图——不属于主体单体
    if _EXCAVATION.search(title):
        return UnitAssignment(UNIT_NOT_APPLICABLE, None,
                              "围护/基坑/施工阶段图，不属于主体单体")

    # ③ 非几何与详图——本就没有单体归属（判据来自国标术语，非图号）
    role = classify_role(drawing).role
    if role == ROLE_NON_GEOMETRIC:
        return UnitAssignment(UNIT_NOT_APPLICABLE, None,
                              "目录/说明/表/系统图，本就无单体归属")
    if role == ROLE_DETAIL:
        return UnitAssignment(UNIT_NOT_APPLICABLE, None,
                              "详图画通用做法、跨单体复用，强行归属反而是错的")

    # ④ 有楼层却缺单体——降级挂默认单体（这是 778 张真损失的救法）
    if _FLOOR_HINT.search(title):
        return UnitAssignment(
            UNIT_DEFAULTED, DEFAULT_UNIT_KEY,
            "有楼层线索但图名未写单体，降级挂默认单体（可事后纠正）")

    # ⑤ 既无单体又无楼层——如实标注，不硬挂
    return UnitAssignment(UNIT_UNRESOLVED, None, "既无单体线索也无楼层线索")


def summarize_assignments(drawings: list[Mapping[str, object]]) -> dict:
    """汇总，**把「本就没有」与「该有却没有」分开**。

    混在一起报「80.8% 未分配」会让人去优化一个不存在的问题。
    `needs_attention` 只算 `defaulted + unresolved`。
    """
    counts = {UNIT_ASSIGNED: 0, UNIT_DEFAULTED: 0,
              UNIT_NOT_APPLICABLE: 0, UNIT_UNRESOLVED: 0}
    for drawing in drawings:
        counts[classify_unit_assignment(drawing).status] += 1
    return {
        "assigned": counts[UNIT_ASSIGNED],
        "defaulted": counts[UNIT_DEFAULTED],
        "not_applicable": counts[UNIT_NOT_APPLICABLE],
        "unresolved": counts[UNIT_UNRESOLVED],
        "needs_attention": counts[UNIT_DEFAULTED] + counts[UNIT_UNRESOLVED],
    }
