"""Phase H 真实度提升:构件截面模数化对齐(dimension snapping)。纯函数。

**问题(大歌剧院实测)**:柱截面 46.5% 偏离 50mm 模数超 10mm,出现 124 种不同柱宽
(0.51/0.58/0.73/0.99/1.14…)。真实施工图的构件截面必然模数化(500/600/800mm),
这些"零头"是几何提取的比例尺换算 + 轮廓像素抖动产物,直接损害模型真实度。

**方案**:把截面吸附到工程模数,但**只吸附小偏差**——
- 偏差 ≤ SNAP_TOLERANCE_M(默认 30mm):吸附(抖动修正);
- 偏差 > 容差:**不动**(可能是真实异形构件/严重误检,强行吸附会掩盖真相)。
优先吸附到常用标准截面,其次 50mm 模数。吸附结果带 provenance(snapped 标记)。
"""
from __future__ import annotations

# 常用标准柱/墙截面(米)——优先吸附目标
STANDARD_SECTIONS_M = (
    0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70,
    0.75, 0.80, 0.90, 1.00, 1.10, 1.20, 1.30, 1.40, 1.50, 1.60, 1.80, 2.00,
)
# 模数步进(米):标准截面未命中时按此吸附
MODULE_STEP_M = 0.05
# 最大吸附容差(米):超出不吸附(保留真实异形/误检原貌,不掩盖)
SNAP_TOLERANCE_M = 0.03


def snap_dimension(
    value: float,
    tolerance_m: float = SNAP_TOLERANCE_M,
    step_m: float = MODULE_STEP_M,
) -> tuple[float, bool]:
    """单个尺寸 → (吸附后尺寸, 是否吸附)。

    先找最近标准截面;若其偏差在容差内则吸附,否则退到 step 模数;仍超容差 → 原值不动。
    """
    if value is None or value <= 0:
        return value, False
    nearest_std = min(STANDARD_SECTIONS_M, key=lambda s: abs(s - value))
    if abs(nearest_std - value) <= tolerance_m:
        return round(nearest_std, 3), True
    stepped = round(value / step_m) * step_m
    if stepped > 0 and abs(stepped - value) <= tolerance_m:
        return round(stepped, 3), True
    return value, False


def snap_outline(
    outline: list, tolerance_m: float = SNAP_TOLERANCE_M,
) -> tuple[list, bool]:
    """矩形轮廓按包围盒宽高模数化:等比缩放到吸附后尺寸(保持中心不变)。

    仅处理有 ≥3 点的轮廓;宽或高任一被吸附即返回 snapped=True。非矩形轮廓同样
    按包围盒缩放(保守:形状不变,仅尺寸对齐模数)。
    """
    pts = [p for p in (outline or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
    if len(pts) < 3:
        return outline, False
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if w <= 0 or h <= 0:
        return outline, False
    new_w, snapped_w = snap_dimension(w, tolerance_m)
    new_h, snapped_h = snap_dimension(h, tolerance_m)
    if not (snapped_w or snapped_h):
        return outline, False
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    fx, fy = new_w / w, new_h / h
    return [
        [round(cx + (float(p[0]) - cx) * fx, 4), round(cy + (float(p[1]) - cy) * fy, 4)]
        for p in pts
    ], True


def snap_instances(instances: list[dict], tolerance_m: float = SNAP_TOLERANCE_M) -> dict:
    """批量模数化实体的 outline_m(点状构件柱/桩/设备最受益)。

    返回 {snapped, total} 统计;实体就地更新 outline_m 并标 `dimension_snapped`。
    """
    snapped = 0
    for inst in instances:
        outline, ok = snap_outline(inst.get("outline_m"), tolerance_m)
        if ok:
            inst["outline_m"] = outline
            inst["dimension_snapped"] = True
            snapped += 1
    return {"snapped": snapped, "total": len(instances)}


def snap_scene_columns(floors: list[dict], tolerance_m: float = SNAP_TOLERANCE_M) -> dict:
    """对 scene 各楼层的**柱截面**做模数化(3D 渲染直接读 scene.elements,故须在此生效)。

    **仅柱/桩**:它们的截面在施工图中必然模数化(500/600/800mm),实测抖动最严重
    (124 种柱宽 → 模数化后 32 种)。**不动** slabs(板轮廓=建筑外形)与
    walls/beams 的 path(=构件走向),避免破坏真实几何形状。
    """
    snapped = 0
    total = 0
    for floor in floors or []:
        for column in (floor.get("elements") or {}).get("columns") or []:
            outline = column.get("outline")
            if not outline:
                continue
            total += 1
            new_outline, ok = snap_outline(outline, tolerance_m)
            if ok:
                column["outline"] = new_outline
                column["dimension_snapped"] = True
                snapped += 1
    return {"snapped": snapped, "total": total}


def module_compliance(values: list[float], step_m: float = MODULE_STEP_M) -> dict:
    """模数符合度量:偏离 step 模数的 mm 偏差分位数 + 超 10mm 占比 + 尺寸种类数。

    用于量化「真实度」提升(吸附前后对比)。
    """
    devs = sorted(abs(v - round(v / step_m) * step_m) * 1000 for v in values if v and v > 0)
    if not devs:
        return {"n": 0, "p50_mm": 0.0, "p90_mm": 0.0, "over_10mm_pct": 0.0, "distinct": 0}
    n = len(devs)
    return {
        "n": n,
        "p50_mm": round(devs[n // 2], 2),
        "p90_mm": round(devs[min(int(n * 0.9), n - 1)], 2),
        "over_10mm_pct": round(100 * sum(1 for d in devs if d > 10) / n, 2),
        "distinct": len({round(v, 2) for v in values if v and v > 0}),
    }
