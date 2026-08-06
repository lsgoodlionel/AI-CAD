"""Phase H3 收敛精修:工程约束求解(蓝图 §3 step 3-4)。纯函数。

装配得到关联后的实体后,用工程守恒律做后处理,让误差收敛而非累积:
- apply_floor_z    竖向:盖真实标高(仅真实源,严禁默认套),留 provenance;
- snap_to_grid     平面:点状构件(柱/桩)吸附到轴网交点,消位置抖动;
- check_vertical_continuity  跨层:同轴号柱/桩应贯通,报缺层;
- reconcile_with_bom  数量:识别数对齐构件表设计数(BOM 来自 H5 大模型)。
"""
from __future__ import annotations

from typing import Any

# 有真实来源的竖向 provenance(其余如 story_default/None 留 NULL,保竖向真实率可度量)。
# floor_elevation = 楼层级已建立的真实标高(源自剖面恢复/人工录入/规整),用于盖构件 Z
# (楼层级真实,区别于"每构件默认套")。
_REAL_Z_SOURCES = ("section", "elevation", "manual", "floor_elevation")
# 吸附/连续默认作用的点状构件
_POINT_TYPES = ("column", "pile")


def is_real_z_source(z_source: str | None) -> bool:
    """该竖向来源是否算「真实」(H7 竖向真实率口径)。default 套值不算。"""
    return z_source in _REAL_Z_SOURCES


def apply_floor_z(
    instances: list[dict], z_bottom_m: float | None, z_top_m: float | None,
    z_source: str | None,
) -> list[dict]:
    """给一层实体盖标高 + **如实记录竖向 provenance**。

    标高为空 → 不盖(留 NULL)。标高非空 → 盖值,并按真实来源记 z_source:
    - section/elevation/manual:真实标高(计入竖向真实率);
    - story_default:系统默认套层高(4.2/4.5)——**仍盖值**(否则整栋构件无竖向位置、
      模型塌到 0 标高),但 z_source 如实标 story_default,**不计入真实率**。
    诚实原则:宁可低报真实率,也不让默认套冒充真实标高。
    """
    if z_bottom_m is None:
        return instances
    for inst in instances:
        inst["z_bottom_m"] = z_bottom_m
        inst["z_top_m"] = z_top_m
        inst["z_source"] = z_source or "story_default"
    return instances


def _grid_maps(axes: dict) -> tuple[dict, dict]:
    xmap = {str(e.get("label")): float(e["coord"]) for e in axes.get("x") or [] if "coord" in e}
    ymap = {str(e.get("label")): float(e["coord"]) for e in axes.get("y") or [] if "coord" in e}
    return xmap, ymap


def snap_to_grid(
    instances: list[dict], axes: dict | None, types: tuple = _POINT_TYPES,
) -> list[dict]:
    """点状构件(柱/桩)按 grid_ref 吸附到轴网交点:平移 outline_m 使质心落在交点。

    仅处理有 grid_ref、grid_ref 两端轴号在 axes 中都能定位、且有 outline_m 的实体。
    标记 `snapped=True`。缺 axes 直接返回(不动)。
    """
    if not axes:
        return instances
    xmap, ymap = _grid_maps(axes)
    for inst in instances:
        if inst.get("type") not in types:
            continue
        grid = inst.get("grid_ref")
        outline = inst.get("outline_m")
        if not grid or "-" not in grid or not outline:
            continue
        x_label, y_label = grid.split("-", 1)
        tx, ty = xmap.get(x_label), ymap.get(y_label)
        if tx is None or ty is None:
            continue
        pts = [p for p in outline if len(p) >= 2]
        if not pts:
            continue
        cx = sum(float(p[0]) for p in pts) / len(pts)
        cy = sum(float(p[1]) for p in pts) / len(pts)
        dx, dy = tx - cx, ty - cy
        inst["outline_m"] = [[round(float(p[0]) + dx, 3), round(float(p[1]) + dy, 3)] for p in pts]
        inst["snapped"] = True
    return instances


def check_vertical_continuity(
    instances_by_floor: list[tuple[int, list[dict]]], types: tuple = _POINT_TYPES,
) -> list[dict]:
    """跨层连续性:同 (type, grid_ref) 的柱/桩若出现在非连续楼层区间(中间缺层),
    报缺口。instances_by_floor 为 [(floor_order, instances)];返回缺口清单
    [{type, grid_ref, present_orders, missing_orders}]。
    """
    presence: dict[tuple, set[int]] = {}
    for order, instances in instances_by_floor:
        for inst in instances:
            if inst.get("type") not in types or not inst.get("grid_ref"):
                continue
            presence.setdefault((inst["type"], inst["grid_ref"]), set()).add(order)
    gaps: list[dict] = []
    for (ctype, grid), orders in presence.items():
        lo, hi = min(orders), max(orders)
        missing = [o for o in range(lo, hi + 1) if o not in orders]
        if missing:
            gaps.append({
                "type": ctype, "grid_ref": grid,
                "present_orders": sorted(orders), "missing_orders": missing,
            })
    return gaps


def reconcile_with_bom(instances: list[dict], bom: dict[str, int]) -> dict[str, dict]:
    """数量对齐:实体按 type 计数 vs 构件表 BOM 期望数,返回每型 {expected,actual,diff}。

    diff>0 = 少识别(漏),diff<0 = 多识别(可能重复/误检)。bom 缺的型不报。
    """
    actual: dict[str, int] = {}
    for inst in instances:
        t = inst.get("type")
        if t:
            actual[t] = actual.get(t, 0) + 1
    report: dict[str, dict] = {}
    for ctype, expected in bom.items():
        got = actual.get(ctype, 0)
        report[ctype] = {"expected": expected, "actual": got, "diff": expected - got}
    return report
