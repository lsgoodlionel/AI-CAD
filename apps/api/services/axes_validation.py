"""轴网合理性校验 —— 防止「轴线远离模型主体」。纯函数。

**实测教训**:把「该层轴号最多的那张图」的轴网直接注入楼层,**未校验该图坐标系
与楼层构件坐标系是否一致**(各图 `drawing_transform` 原点不同)→ B1 层轴网跑到
x[-76.7, 960.1]、y[720.6, 1306.6],而构件只在 x[-4.4,123.5]、y[-35.1,144],
偏离 700+ 米;FD/B3/F3/F6 偏离 100–226m。此时 grid_cell 也随之全错。

**校验准则**(工程事实):轴网是构件的定位基准,二者必然**大致重合**——
轴网范围应落在构件包络(含合理外扩)之内,且能覆盖构件的主体范围。
"""
from __future__ import annotations

#: 轴网中心与构件中心的最大错位，按构件跨度的倍数。
#:
#: **为什么不比「边界包含」**：轴网**本来就该比构件大** ——
#: §8.0.2 轴号圈画在轴线端部（建筑轮廓之外），而构件识别每层只取 2 张图、
#: 包络必然不完整。实测 RF(轴网 x203 / 构件 x100)、F6(x223/x98)、
#: B2(x140/x95) 三层都因此被误判为「远离构件」。
#:
#: 而本判据当初要挡的那个案例是**中心错位 700+ 米**
#: （B1 轴网 x[−76.7, 960.1] vs 构件 x[−4.4, 123.5]）—— 那才是坐标系不一致。
MAX_CENTRE_OFFSET_RATIO = 1.0

#: 中心错位的绝对下限（米）：小体量楼层按比例算过于苛刻。
MIN_CENTRE_OFFSET_M = 30.0

#: 轴网跨度相对构件跨度的允许区间。
#: 上限防「比例错了一个数量级」，下限防「局部详图轴网代表不了整层」。
MAX_SPAN_RATIO = 5.0
MIN_SPAN_RATIO = 0.25

#: 求构件包络时两端各裁掉的比例。`min/max` 对离群点没有抵抗力。
OUTLIER_TRIM_RATIO = 0.02

#: 少于这么多点时不裁 —— 分位数在小样本上没有意义。
MIN_POINTS_FOR_TRIM = 20


def _robust_range(values: list[float]) -> tuple[float, float]:
    """稳健的 (下界, 上界)：两端各裁掉 `OUTLIER_TRIM_RATIO`。"""
    if len(values) < MIN_POINTS_FOR_TRIM:
        return min(values), max(values)
    ordered = sorted(values)
    cut = max(1, int(len(ordered) * OUTLIER_TRIM_RATIO))
    return ordered[cut], ordered[-1 - cut]


def elements_bounds(elements: dict) -> tuple[float, float, float, float] | None:
    """楼层构件包络 (min_x, max_x, min_y, max_y);无构件返回 None。"""
    xs: list[float] = []
    ys: list[float] = []
    # **只算结构主体**：GB/T 50001 §8 定位轴线用于确定主要承重构件位置。
    # 实测 F2 层管线 x 范围 −6309~111（机电图比例误差更大），
    # 把包络撑到 6513 米，于是好轴网被判成「跨度过小」。
    # 设备同理：它的位置本就是由楼层包络反推的，不能反过来定义包络。
    for kind in ("columns", "slabs"):
        for item in (elements or {}).get(kind) or []:
            for p in item.get("outline") or []:
                if len(p) >= 2:
                    xs.append(float(p[0]))
                    ys.append(float(p[1]))
    for kind in ("walls", "beams"):
        for item in (elements or {}).get(kind) or []:
            for p in item.get("path") or []:
                if len(p) >= 2:
                    xs.append(float(p[0]))
                    ys.append(float(p[1]))
    if len(xs) < 2:
        return None
    return (*_robust_range(xs), *_robust_range(ys))


def axes_bounds(axes: dict) -> tuple[float, float, float, float] | None:
    """轴网范围 (min_x, max_x, min_y, max_y);需双向都有轴线,否则 None。"""
    xc = [float(a["coord"]) for a in (axes or {}).get("x") or [] if "coord" in a]
    yc = [float(a["coord"]) for a in (axes or {}).get("y") or [] if "coord" in a]
    if not xc or not yc:
        return None
    return min(xc), max(xc), min(yc), max(yc)


def axes_plausible(axes: dict, elements: dict) -> tuple[bool, str]:
    """轴网是否与构件坐标系自洽 → (是否可用, 原因)。

    三条判据：

    1. **中心错位**不得超过构件跨度（下限 30 米）——
       坐标系不一致时中心会差出数量级（实测 700+ 米）。
    2. **尺度比**落在 [0.25, 5.0]：上限防比例错一个数量级，
       下限防局部详图轴网冒充整层。
    3. 必须**双向**：单向轴网定不出交点。

    **不比「边界包含」**：轴网比构件大是常态（§8.0.2 轴号圈在轮廓外、
    构件识别不完整），按边界判会把好轴网大批误杀。
    """
    eb = elements_bounds(elements)
    ab = axes_bounds(axes)
    if eb is None:
        return False, "楼层无构件,无法校验"
    if ab is None:
        return False, "轴网非双向(缺 x 或 y)"
    ex0, ex1, ey0, ey1 = eb
    ax0, ax1, ay0, ay1 = ab
    espan_x, espan_y = max(ex1 - ex0, 1e-6), max(ey1 - ey0, 1e-6)
    aspan_x, aspan_y = ax1 - ax0, ay1 - ay0

    offset_x = abs((ax0 + ax1) / 2 - (ex0 + ex1) / 2)
    offset_y = abs((ay0 + ay1) / 2 - (ey0 + ey1) / 2)
    limit_x = max(espan_x * MAX_CENTRE_OFFSET_RATIO, MIN_CENTRE_OFFSET_M)
    limit_y = max(espan_y * MAX_CENTRE_OFFSET_RATIO, MIN_CENTRE_OFFSET_M)
    if offset_x > limit_x or offset_y > limit_y:
        return False, (
            f"轴网与构件中心错位 x{offset_x:.0f}m y{offset_y:.0f}m"
            f"(限 x{limit_x:.0f} y{limit_y:.0f}),坐标系不一致")

    for label, aspan, espan in (("x", aspan_x, espan_x), ("y", aspan_y, espan_y)):
        ratio = aspan / espan
        if ratio > MAX_SPAN_RATIO:
            return False, f"轴网 {label} 跨度是构件的 {ratio:.1f} 倍,疑为比例错误"
        if ratio < MIN_SPAN_RATIO:
            return False, f"轴网 {label} 跨度只有构件的 {ratio:.0%},疑为局部详图轴网"
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
