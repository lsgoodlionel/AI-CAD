"""轴网合理性校验 —— 防止「轴线远离模型主体」。纯函数。

**实测教训**:把「该层轴号最多的那张图」的轴网直接注入楼层,**未校验该图坐标系
与楼层构件坐标系是否一致**(各图 `drawing_transform` 原点不同)→ B1 层轴网跑到
x[-76.7, 960.1]、y[720.6, 1306.6],而构件只在 x[-4.4,123.5]、y[-35.1,144],
偏离 700+ 米;FD/B3/F3/F6 偏离 100–226m。此时 grid_cell 也随之全错。

**校验准则**(工程事实):轴网是构件的定位基准,二者必然**大致重合**——
轴网范围应落在构件包络(含合理外扩)之内,且能覆盖构件的主体范围。
"""
from __future__ import annotations

#: 轴网允许超出构件包络的比例(轴线常越出建筑轮廓一段作标注)
OUTSIDE_MARGIN_RATIO = 0.35
#: 轴网允许超出的绝对下限(米):小体量楼层按比例算过于苛刻
OUTSIDE_MARGIN_MIN_M = 20.0
#: 轴网跨度至少覆盖构件跨度的比例(过小说明只是局部详图的轴网)
MIN_COVERAGE_RATIO = 0.25


def elements_bounds(elements: dict) -> tuple[float, float, float, float] | None:
    """楼层构件包络 (min_x, max_x, min_y, max_y);无构件返回 None。"""
    xs: list[float] = []
    ys: list[float] = []
    for kind in ("columns", "slabs", "equipment"):
        for item in (elements or {}).get(kind) or []:
            for p in item.get("outline") or []:
                if len(p) >= 2:
                    xs.append(float(p[0]))
                    ys.append(float(p[1]))
    for kind in ("walls", "beams", "pipes"):
        for item in (elements or {}).get(kind) or []:
            for p in item.get("path") or []:
                if len(p) >= 2:
                    xs.append(float(p[0]))
                    ys.append(float(p[1]))
    if len(xs) < 2:
        return None
    return min(xs), max(xs), min(ys), max(ys)


def axes_bounds(axes: dict) -> tuple[float, float, float, float] | None:
    """轴网范围 (min_x, max_x, min_y, max_y);需双向都有轴线,否则 None。"""
    xc = [float(a["coord"]) for a in (axes or {}).get("x") or [] if "coord" in a]
    yc = [float(a["coord"]) for a in (axes or {}).get("y") or [] if "coord" in a]
    if not xc or not yc:
        return None
    return min(xc), max(xc), min(yc), max(yc)


def axes_plausible(axes: dict, elements: dict) -> tuple[bool, str]:
    """轴网是否与构件坐标系自洽 → (是否可用, 原因)。

    两条硬性判据:
    1. **不得远离**:轴网范围须落在构件包络 + 外扩容差内(容差 = 跨度×35%,下限 20m);
    2. **须有覆盖**:轴网跨度至少达构件跨度的 25%(否则只是局部详图轴网,不能代表整层)。
    """
    eb = elements_bounds(elements)
    ab = axes_bounds(axes)
    if eb is None:
        return False, "楼层无构件,无法校验"
    if ab is None:
        return False, "轴网非双向(缺 x 或 y)"
    ex0, ex1, ey0, ey1 = eb
    ax0, ax1, ay0, ay1 = ab
    span_x, span_y = ex1 - ex0, ey1 - ey0
    mx = max(span_x * OUTSIDE_MARGIN_RATIO, OUTSIDE_MARGIN_MIN_M)
    my = max(span_y * OUTSIDE_MARGIN_RATIO, OUTSIDE_MARGIN_MIN_M)
    if ax0 < ex0 - mx or ax1 > ex1 + mx or ay0 < ey0 - my or ay1 > ey1 + my:
        return False, (
            f"轴网远离构件:轴网 x[{ax0:.0f},{ax1:.0f}] y[{ay0:.0f},{ay1:.0f}] "
            f"vs 构件 x[{ex0:.0f},{ex1:.0f}] y[{ey0:.0f},{ey1:.0f}](坐标系不一致)")
    if span_x > 0 and (ax1 - ax0) < span_x * MIN_COVERAGE_RATIO:
        return False, "轴网 x 跨度过小,疑为局部详图轴网"
    if span_y > 0 and (ay1 - ay0) < span_y * MIN_COVERAGE_RATIO:
        return False, "轴网 y 跨度过小,疑为局部详图轴网"
    return True, "轴网与构件坐标系自洽"


def filter_scene_axes(floors: list[dict]) -> dict:
    """对 scene 各层轴网做合理性校验,**剔除坐标系不一致者**(置 None)。

    返回 {kept, dropped, details}。宁可无轴网(降级米坐标兜底),也不要错轴网——
    错轴网既让 3D 显示错乱,又让 grid_cell 关联主键全错。
    """
    kept = 0
    dropped: list[dict] = []
    for floor in floors or []:
        axes = floor.get("axes")
        if not axes:
            continue
        # 人工标定的基准是**人核过的真值**,不受自动合理性校验否决
        # (校验是为挡住自动识别的错轴网,不该推翻人的判断)
        if floor.get("axes_source") == "manual":
            kept += 1
            continue
        ok, reason = axes_plausible(axes, floor.get("elements") or {})
        if ok:
            kept += 1
        else:
            floor["axes"] = None
            dropped.append({"floor": str(floor.get("key")), "reason": reason})
    return {"kept": kept, "dropped": len(dropped), "details": dropped}
