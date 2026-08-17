"""Phase H3:观测 → ComponentInstance 集(数据关联 + 聚合估计 + 冲突标记)。纯函数。

范式核心(蓝图 §3):把多张图的观测装配成带身份的构件实体,误差靠多观测相互
校验 + 约束收敛,而非碎片堆叠累积。

- 关联主键:`(type, grid_cell)` 优先(轴号跨视图统一,比米坐标鲁棒);grid_cell
  缺失时用米坐标近邻兜底(同类型 + 阈值内)。
- 门控:类型必须一致,宁分裂待人审合并,不错并造假构件(呼应 G9 止血)。
- 聚合:多观测 → 代表轮廓 + 概率 OR 置信(独立证据叠加)+ type_label 投票。
- 冲突:置信低于阈值 → review_state='conflict',进人审队列。

输出 dict 对齐 migration 033 component_instances,并挂 `observations` 列表(供 H3 持久化)。
z_* / section 在本步留空(NULL,严禁默认套),由后续 Z 恢复回填。
"""
from __future__ import annotations

import math
from typing import Any

# 同类构件质心合并阈值(米):无轴网格时,近于此距离视为同一构件
DEFAULT_MERGE_DIST_M = 1.0
# 置信低于此值 → 标 conflict 进人审
CONFLICT_CONFIDENCE = 0.5
#: **可用轴网格作合并主键的构件类型**:柱/桩位于轴网交点,一格一根成立。
#: 墙/梁/板/管线/设备一格内有多个,只能用米坐标聚类(否则整格并成一个)。
GRID_KEYED_TYPES = ("column", "pile")


def _dist(a: list, b: list) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _combine_confidence(confidences: list[float]) -> float:
    """概率 OR 叠加独立观测证据:1 - ∏(1 - c)。观测越多、越一致,置信越高。"""
    prod = 1.0
    for c in confidences:
        prod *= (1.0 - max(0.0, min(1.0, float(c))))
    return round(1.0 - prod, 4)


def _vote_type_label(observations: list[dict]) -> str | None:
    """type_label 多数投票(忽略空);并列取观测多的首个。"""
    tally: dict[str, int] = {}
    for obs in observations:
        label = obs.get("type_label")
        if label:
            tally[label] = tally.get(label, 0) + 1
    if not tally:
        return None
    return max(tally.items(), key=lambda kv: kv[1])[0]


def _representative_outline(observations: list[dict]) -> list | None:
    """代表轮廓:取置信最高且有 local_coord 的观测(简单稳健,H 后续可换加权融合)。"""
    best = None
    best_conf = None
    for obs in observations:
        coord = obs.get("local_coord")
        if not coord:
            continue
        conf = float(obs.get("confidence") or 0.0)
        if best_conf is None or conf > best_conf:
            best_conf = conf
            best = coord
    return best


def _centroid_of(observations: list[dict]) -> list[float] | None:
    """实体质心:所有观测 world_coord 的均值(用于米坐标兜底关联)。"""
    pts = [o["world_coord"] for o in observations if o.get("world_coord")]
    if not pts:
        return None
    n = len(pts)
    return [
        round(sum(float(p[0]) for p in pts) / n, 3),
        round(sum(float(p[1]) for p in pts) / n, 3),
    ]


def _new_instance(obs: dict, building_key: str, floor_key: str = "") -> dict:
    from services.component_observations import is_full_grid
    grid = obs.get("grid_cell")
    # 语义键须含**单体+楼层**维度(蓝图原设计 "col:unitA:C-3"):同轴网格在不同楼层
    # 是不同构件,只用 type@grid 会跨层碰撞(实测轴网生效后 pipe@3-BY 唯一约束冲突)。
    return {
        # 仅点状构件(柱/桩)+ 完整网格才有唯一语义键;其他类型同格多个会碰撞
        "semantic_key": (f"{obs['type']}@{building_key}:{floor_key}:{grid}"
                         if is_full_grid(grid) and obs["type"] in GRID_KEYED_TYPES
                         else None),
        "building_key": building_key,
        "type": obs["type"],
        "grid_ref": grid,       # 完整或部分都记录(配准覆盖/显示/追溯)
        "observations": [obs],
    }


def _finalize(inst: dict) -> dict:
    """聚合估计 + 冲突判定,补齐 component_instances 字段。"""
    obs = inst["observations"]
    # 语义键补质心坐标:实测**一格多柱**(柱 3469 vs 网格 ~440),仅 type@单体:层:格
    # 无法唯一标识构件(触发 uq_ci_semantic 冲突)。质心由观测决定,稳定且唯一。
    if inst.get("semantic_key"):
        centroid = _centroid_of(obs)
        if centroid:
            inst["semantic_key"] = f"{inst['semantic_key']}@{centroid[0]},{centroid[1]}"
    confidence = _combine_confidence([o.get("confidence") or 0.0 for o in obs])
    inst["outline_m"] = _representative_outline(obs)
    inst["type_label"] = _vote_type_label(obs)
    inst["confidence"] = confidence
    inst["review_state"] = "auto" if confidence >= CONFLICT_CONFIDENCE else "conflict"
    # Z / 截面留空(NULL),严禁默认套
    inst["z_bottom_m"] = None
    inst["z_top_m"] = None
    inst["z_source"] = None
    inst["section_json"] = None
    return inst


def assemble_instances(
    observations: list[dict],
    building_key: str = "",
    merge_dist_m: float = DEFAULT_MERGE_DIST_M,
    floor_key: str = "",
) -> list[dict]:
    """观测候选 → ComponentInstance 集(数据关联 + 聚合)。

    高置信观测先锚定;`(type, grid_cell)` 命中即累积,否则米坐标近邻兜底,
    都不中则新建实体(门控:类型一致才合并)。
    """
    from services.component_observations import is_full_grid

    instances: list[dict] = []
    by_grid: dict[tuple, list] = {}

    for obs in sorted(observations, key=lambda o: -(o.get("confidence") or 0.0)):
        grid = obs.get("grid_cell")
        # **只有点状构件(柱/桩)能以轴网格作合并主键**:轴网语义是"柱在 C-3 交点",
        # 一格一柱成立;而墙/梁/管线/设备在**一格内有很多**(一格约 6×6m),
        # 若也用格作主键会把整格构件并成一个(实测:实体 8094→1059、单实体 722 观测)。
        full = is_full_grid(grid) and obs.get("type") in GRID_KEYED_TYPES
        target: dict | None = None

        if full:
            # **格内候选 + 米距门控**(蓝图 §3 gate_ok):同格同类型还须位置相近才是
            # 同一构件。轴网格约 6×6m,实测柱数(3469)远多于网格数(~440)→ 一格多柱;
            # 仅凭格合并会把整格柱并成一根(实测柱 3469→341)。故每格保留**候选列表**,
            # 取米距最近且在阈值内者;缺坐标时退回"同格即同构件"(无更强证据)。
            bucket = by_grid.setdefault((obs["type"], grid), [])
            wc = obs.get("world_coord")
            best_d = None
            for cand in bucket:
                ic = _centroid_of(cand["observations"])
                if wc and ic:
                    d = _dist(wc, ic)
                    if d <= merge_dist_m and (best_d is None or d < best_d):
                        best_d, target = d, cand
                elif target is None:
                    target = cand      # 无坐标可比 → 同格即同构件
        else:
            # 无网格 或 部分网格(X-?)→ 米坐标兜底(同类型 + 阈值内最近),防单轴狂并
            wc = obs.get("world_coord")
            if wc:
                best_d = None
                for inst in instances:
                    if inst["type"] != obs["type"]:
                        continue
                    ic = _centroid_of(inst["observations"])
                    if ic is None:
                        continue
                    d = _dist(wc, ic)
                    if d <= merge_dist_m and (best_d is None or d < best_d):
                        best_d = d
                        target = inst

        if target is None:
            inst = _new_instance(obs, building_key, floor_key)
            instances.append(inst)
            if full:
                by_grid[(obs["type"], grid)].append(inst)
        else:
            target["observations"].append(obs)

    return [_finalize(inst) for inst in instances]
