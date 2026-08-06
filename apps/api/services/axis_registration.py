"""同名轴线配准 + **配准质量评估** —— 多图拼接成完整模型的关键。纯函数。

## 领域依据(用户指出)

- **常规图纸**:以**相同命名的轴线**即可拼接(图 A 的「5」轴与图 B 的「5」轴是同一条);
- **复杂图纸**:再叠加定位坐标(直接标在轴线上,或另有专门定位图)与轴线结合定位。

## 为什么要评估配准质量(现有实现的短板)

既有 `register_offset` 取共有轴号位置差的**中位数**作平移量,方向正确,但:
1. 只有 **1 个**共有轴号时,中位数就是那一个,**无从校验**却照样平移;
2. 各共有轴号的位置差**互不一致**时(两图比例不同、或轴号误识),中位数会**掩盖**矛盾;
3. 没有残差反馈,坏配准与好配准对下游无差别 → 模型被平移到错位置也无人知。

**本模块**:同名轴线对齐时同时给出**一致性残差**与可信判定——
残差大说明「两图不是简单平移关系」(多为比例不同或轴号误识),此时**拒绝配准**
优于硬平移(错位置比不拼更糟)。
"""
from __future__ import annotations

#: 至少需要的共有轴号数(1 个无法校验一致性)
MIN_SHARED_AXES = 2
#: 共有轴号位置差的最大残差(米):超出说明不是简单平移关系
MAX_RESIDUAL_M = 1.0


def _axis_map(axes: dict, direction: str) -> dict[str, float]:
    """轴网 → {轴号: 坐标};兼容 [[label,pos]] 与 [{"label","coord"}] 两种结构。"""
    out: dict[str, float] = {}
    for entry in (axes or {}).get(direction) or []:
        if isinstance(entry, dict):
            label, pos = entry.get("label"), entry.get("coord")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            label, pos = entry[0], entry[1]
        else:
            continue
        if label is None or pos is None:
            continue
        key = str(label).strip()
        if key:
            out[key] = float(pos)
    return out


def _offset_with_residual(
    ref: dict[str, float], cur: dict[str, float],
) -> tuple[float, float, int]:
    """共有轴号 → (平移量中位数, 最大残差, 共有轴号数)。

    残差 = 各共有轴号按中位数平移后与参考的最大偏差 —— 衡量「是否真是平移关系」。
    """
    shared = sorted(ref.keys() & cur.keys())
    if not shared:
        return 0.0, 0.0, 0
    deltas = sorted(ref[k] - cur[k] for k in shared)
    median = deltas[len(deltas) // 2]
    residual = max(abs(d - median) for d in deltas)
    return median, residual, len(shared)


def align_by_shared_axes(
    ref_axes: dict, axes: dict,
    min_shared: int = MIN_SHARED_AXES, max_residual_m: float = MAX_RESIDUAL_M,
) -> dict:
    """以参考轴网为基准配准当前图 → 平移量 + 质量。

    返回 {ok, dx, dy, shared_x, shared_y, residual_x, residual_y, reason}。
    ok=False 时**不应**平移(拒绝配准优于错位拼接)。
    """
    dx, res_x, n_x = _offset_with_residual(_axis_map(ref_axes, "x"), _axis_map(axes, "x"))
    dy, res_y, n_y = _offset_with_residual(_axis_map(ref_axes, "y"), _axis_map(axes, "y"))

    reasons: list[str] = []
    if n_x + n_y == 0:
        reasons.append("无同名轴线,无法配准")
    else:
        # 任一方向有共有轴号即可给该方向平移;但方向内共有数不足则该方向不可信
        if 0 < n_x < min_shared:
            reasons.append(f"x 向仅 {n_x} 条同名轴线,无法校验一致性")
        if 0 < n_y < min_shared:
            reasons.append(f"y 向仅 {n_y} 条同名轴线,无法校验一致性")
        if n_x >= min_shared and res_x > max_residual_m:
            reasons.append(f"x 向残差 {res_x:.2f}m 过大(非平移关系:比例不同或轴号误识)")
        if n_y >= min_shared and res_y > max_residual_m:
            reasons.append(f"y 向残差 {res_y:.2f}m 过大(非平移关系:比例不同或轴号误识)")

    ok = not reasons and (n_x >= min_shared or n_y >= min_shared)
    return {
        "ok": ok,
        "dx": round(dx, 3) if n_x >= min_shared else 0.0,
        "dy": round(dy, 3) if n_y >= min_shared else 0.0,
        "shared_x": n_x, "shared_y": n_y,
        "residual_x": round(res_x, 3), "residual_y": round(res_y, 3),
        "reason": "; ".join(reasons) or "同名轴线配准一致",
    }


def merge_axes(ref_axes: dict, axes: dict, dx: float, dy: float) -> dict:
    """把当前图轴网按 (dx,dy) 平移后并入参考轴网(同名取参考值,新轴号补入)。

    多图轴网并集能让整层轴网更完整——单图往往只画本区域的轴线。
    """
    merged = {"x": dict(_axis_map(ref_axes, "x")), "y": dict(_axis_map(ref_axes, "y"))}
    for label, pos in _axis_map(axes, "x").items():
        merged["x"].setdefault(label, round(pos + dx, 3))
    for label, pos in _axis_map(axes, "y").items():
        merged["y"].setdefault(label, round(pos + dy, 3))
    return {
        "x": [{"label": k, "coord": v} for k, v in sorted(merged["x"].items(), key=lambda kv: kv[1])],
        "y": [{"label": k, "coord": v} for k, v in sorted(merged["y"].items(), key=lambda kv: kv[1])],
    }


#: 真实轴线的最小间距(米):小于此说明是同一处文字被误识,非真轴线
MIN_AXIS_SPACING_M = 2.0
#: 一组轴线至少要跨越的范围(米):跨度过小说明挤在一处(如图框文字)
MIN_AXIS_SPAN_M = 10.0


def filter_real_axes(axes: dict, direction: str) -> list[tuple[str, float]]:
    """剔除**假轴号**,返回 [(label, coord)](按坐标排序)。

    **实测问题**:OCR 会把图框/标题栏里的数字(图号 `A-10-04.1C` 中的 0/1/3/4)
    误识为轴号,其坐标全挤在同一处(实测「0」169.44、「1」168.97、「3」168.51、
    「4」170.38,彼此不到 2 米)。真实轴网的轴线间距在米级(常 6–8m)且跨越整个建筑。

    过滤:① 相邻间距 < MIN_AXIS_SPACING_M 的成簇轴号整簇丢弃(挤在一处 = 非轴线);
         ② 过滤后总跨度 < MIN_AXIS_SPAN_M 视为无效轴网。
    """
    items = sorted(_axis_map(axes, direction).items(), key=lambda kv: kv[1])
    if len(items) < 2:
        return []
    # 按间距分簇:间距 >= 阈值处断开
    clusters: list[list[tuple[str, float]]] = [[items[0]]]
    for label, pos in items[1:]:
        if pos - clusters[-1][-1][1] >= MIN_AXIS_SPACING_M:
            clusters.append([(label, pos)])
        else:
            clusters[-1].append((label, pos))
    # 每簇只保留一个代表(簇内挤在一起,至多算一条轴线);单点簇即正常轴线
    kept = [c[0] for c in clusters]
    if len(kept) < 2 or (kept[-1][1] - kept[0][1]) < MIN_AXIS_SPAN_M:
        return []
    return kept


def clean_axes(axes: dict) -> dict:
    """对轴网双向做假轴号过滤,返回同结构的干净轴网(不合格方向为空)。"""
    return {
        "x": [{"label": l, "coord": c} for l, c in filter_real_axes(axes, "x")],
        "y": [{"label": l, "coord": c} for l, c in filter_real_axes(axes, "y")],
    }
