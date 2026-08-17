"""部分图纸建模能力评估与阶段调度。

**为什么必须支持部分图纸**:工程上拿到整套竣工图是少数情况。常见的是
只有几张平面图、只有结构图没有建筑图、或图纸分批到货。
系统若要求「齐了才能建」，大部分时候就用不上。

**设计原则**:

1. 每一阶段缺失时都有明确降级路径，**不阻断**后续阶段;
2. 降级结果**打标记**——绝不让默认值冒充图纸读出来的值;
3. 判据只依赖**角色**(`services/drawing_role.py`)，而角色本身
   不依赖任何工程的图号体系，兜底到国标内容特征。

与 `docs/MODELING_PIPELINE_BLUEPRINT.md` 的 P0~P6 一一对应。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from services.drawing_role import (
    ROLE_COMPONENT_SOURCE, ROLE_COORDINATE_BASE, ROLE_ELEVATION_REFERENCE,
    ROLE_FLOOR_SKELETON, ROLE_STAGE,
)

#: 能力档位。`partial` 的语义是「能出结果但**是降级的**」，
#: 必须让消费方和用户都看得见，不能悄悄按 full 处理。
CAPABILITY_FULL = "full"
CAPABILITY_PARTIAL = "partial"
CAPABILITY_NONE = "none"

#: 进入阶段调度的角色（详图、非几何、unknown 不参与几何建模）。
BUILDABLE_ROLES = (ROLE_COORDINATE_BASE, ROLE_FLOOR_SKELETON,
                   ROLE_ELEVATION_REFERENCE, ROLE_COMPONENT_SOURCE)


@dataclass(frozen=True)
class SetCapability:
    """这批图能建到什么程度。`degradations` 是**要如实告诉用户的话**。"""
    world_coords: str
    floors: str
    elevations: str
    can_build: bool
    degradations: list[str] = field(default_factory=list)


def assess_capability(role_counts: Mapping[str, int]) -> SetCapability:
    """按角色计数评估建模能力，并列出所有降级项。

    `role_counts` 形如 ``{role: 张数}``。
    """
    counts = {role: int(role_counts.get(role) or 0) for role in BUILDABLE_ROLES}
    degradations: list[str] = []

    # P0 世界坐标：没有坐标基准图就只能做相对几何
    if counts[ROLE_COORDINATE_BASE] > 0:
        world = CAPABILITY_FULL
    else:
        world = CAPABILITY_NONE
        degradations.append(
            "无坐标基准图（轴号圈 + 坐标标注）——模型只有相对几何，"
            "**没有世界坐标**，不能与其他单体/测量成果对齐")

    # P1 楼层：完整平面图最好；只有专业平面图时靠图名归纳，是降级
    if counts[ROLE_FLOOR_SKELETON] > 0:
        floors = CAPABILITY_FULL
    elif counts[ROLE_COMPONENT_SOURCE] > 0:
        floors = CAPABILITY_PARTIAL
        degradations.append(
            "无完整平面图——楼层由专业平面图的图名归纳，"
            "可能缺层或分层不准")
    else:
        floors = CAPABILITY_NONE
        degradations.append("无任何平面图——建不出楼层")

    # P2 标高：没有立面/剖面就只能用默认层高
    if counts[ROLE_ELEVATION_REFERENCE] > 0:
        elevations = CAPABILITY_FULL
    else:
        elevations = CAPABILITY_NONE
        degradations.append(
            "无立面/剖面图——层高使用**默认值**，非图纸实测值，"
            "竖向尺寸不可用于算量或碰撞检查")

    can_build = counts[ROLE_FLOOR_SKELETON] > 0 or counts[ROLE_COMPONENT_SOURCE] > 0
    if not can_build:
        degradations.append("没有任何可产出构件的平面图——无法建模")

    return SetCapability(world_coords=world, floors=floors,
                         elevations=elevations, can_build=can_build,
                         degradations=degradations)


def plan_stages(role_counts: Mapping[str, int]) -> list[dict]:
    """按**依赖顺序**排出要跑的阶段。缺的阶段直接跳过，**不阻断**后面的。

    「不阻断」是部分图纸建模的关键:没有坐标基准图，照样可以建楼层与构件，
    只是没有世界坐标——而不是整个建模停摆。
    """
    stages = []
    for role in sorted(BUILDABLE_ROLES, key=lambda r: ROLE_STAGE[r]):
        count = int(role_counts.get(role) or 0)
        if count > 0:
            stages.append({"stage": ROLE_STAGE[role], "role": role,
                           "count": count})
    return stages
