"""从剖面/立面图恢复楼层标高 —— 用「标高↔图上位置线性关系」自校验。纯函数。

## 领域依据(用户指出 + 实测验证)

标高一般标注在**剖面图/立面图**内(常以表格形式)。而剖面/立面图的竖向是**按比例绘制**的,
故图上 y 坐标与标高值必然**严格线性**:

    实测「建筑-东立面图」:y=1348.6→0.000、y=1225.6→10.040、y=1032.7→26.200
    两组独立验算比例:10.040/123.0 = 0.0816 与 26.200/315.9 = 0.0829 米/pt(吻合)

**这条线性关系是最好的自校验**:
- 落在拟合线上的标高 → 可信(位置与数值互证);
- 偏离的 → OCR 误识或局部标高(窗顶/女儿墙),自动剔除;
- 拟合优度 R² 低 → 该图标高整体不可信,不予采用(宁可不给,不给错的)。

## 与既有路径的关系

`plan_elevation_recovery`(平面图投票)质量不足以自动采用;本模块用剖面/立面的
线性约束,质量高得多,且能给出**可解释的置信**(R² + 内点数)。
"""
from __future__ import annotations

#: 拟合优度门槛:低于此判定该图标高整体不可信
MIN_R_SQUARED = 0.98
#: 迭代剔除离群点的残差阈值(米)
OUTLIER_RESIDUAL_M = 0.5
#: 至少需要的内点数(点太少线性关系无意义)
MIN_INLIERS = 4
#: 合理层高区间(米),用于从标高序列中挑主楼面序列
MIN_STORY_HEIGHT_M = 2.5
MAX_STORY_HEIGHT_M = 9.0


def _fit(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """最小二乘拟合 elevation = a * y + b;点不足或退化返回 None。"""
    n = len(points)
    if n < 2:
        return None
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        return None
    a = (n * sxy - sx * sy) / denom
    b = (sy - a * sx) / n
    return a, b


def _r_squared(points: list[tuple[float, float]], a: float, b: float) -> float:
    n = len(points)
    mean = sum(p[1] for p in points) / n
    ss_tot = sum((p[1] - mean) ** 2 for p in points)
    ss_res = sum((p[1] - (a * p[0] + b)) ** 2 for p in points)
    if ss_tot <= 1e-12:
        return 0.0
    return 1.0 - ss_res / ss_tot


def fit_elevation_axis(
    points: list[tuple[float, float]],
    residual_m: float = OUTLIER_RESIDUAL_M,
) -> dict:
    """(y_pt, elevation_m) 点集 → **RANSAC** 线性拟合(抗离群)。

    为何用 RANSAC 而非直接最小二乘:标高来自 OCR,单个严重误识(如把 17.5 读成 99)
    就会把最小二乘拟合线整体拉偏,导致全部点被判离群。RANSAC 先用点对投票选出
    内点最多的模型,再用内点精化,单点误识不影响结论。

    返回 {ok, slope, intercept, r_squared, inliers, dropped}。
    ok=True 表示该图标高与位置自洽(可信)。
    """
    pts = [(float(y), float(e)) for y, e in points or []]
    n = len(pts)
    if n < MIN_INLIERS:
        return {"ok": False, "slope": None, "intercept": None,
                "r_squared": 0.0, "inliers": n, "dropped": 0}

    best_inliers: list[tuple[float, float]] = []
    for i in range(n):
        for j in range(i + 1, n):
            (y1, e1), (y2, e2) = pts[i], pts[j]
            if abs(y1 - y2) < 1e-6:
                continue
            a = (e2 - e1) / (y2 - y1)
            b = e1 - a * y1
            inliers = [p for p in pts if abs(p[1] - (a * p[0] + b)) <= residual_m]
            if len(inliers) > len(best_inliers):
                best_inliers = inliers

    if len(best_inliers) < MIN_INLIERS:
        return {"ok": False, "slope": None, "intercept": None,
                "r_squared": 0.0, "inliers": len(best_inliers),
                "dropped": n - len(best_inliers)}

    fit = _fit(best_inliers)          # 用内点精化
    if fit is None:
        return {"ok": False, "slope": None, "intercept": None,
                "r_squared": 0.0, "inliers": len(best_inliers), "dropped": n - len(best_inliers)}
    a, b = fit
    r2 = _r_squared(best_inliers, a, b)
    return {
        "ok": r2 >= MIN_R_SQUARED and len(best_inliers) >= MIN_INLIERS,
        "slope": a, "intercept": b, "r_squared": round(r2, 5),
        "inliers": len(best_inliers), "dropped": n - len(best_inliers),
    }


def main_story_elevations(
    elevations: list[float],
    min_h: float = MIN_STORY_HEIGHT_M,
    max_h: float = MAX_STORY_HEIGHT_M,
) -> list[float]:
    """从一堆标高中挑出**主楼面序列**(相邻间距落在合理层高区间)。

    剖面/立面上除楼面标高外还有窗顶、女儿墙、台阶等局部标高;楼面标高的特征是
    相邻间距构成合理层高。贪心:从最低点起,每次取下一个使间距落在 [min_h,max_h]
    的标高;若无合法后继则跳过该点继续。
    """
    values = sorted({round(float(e), 3) for e in elevations or []})
    if not values:
        return []
    best: list[float] = []
    for start in range(len(values)):
        seq = [values[start]]
        for v in values[start + 1:]:
            gap = v - seq[-1]
            if min_h <= gap <= max_h:
                seq.append(v)
        if len(seq) > len(best):
            best = seq
    return best


def match_to_floors(
    elevations: list[float], floors: list[dict], tolerance_m: float = 2.0,
) -> dict[str, dict]:
    """把恢复的标高序列匹配到楼层 → {story_key: {elevation_m, delta_m, matched}}。

    floors: [{"story_key", "order", "elevation_m"(现值,可为 None)}]。
    策略:按 order 升序、标高升序,**最近邻匹配**现值(容差内);现值缺失时按序位对应。
    只给匹配得上的层——匹配不上说明该层不在这张剖面覆盖范围内(局部剖面很常见)。
    """
    seq = sorted(float(e) for e in elevations or [])
    if not seq:
        return {}
    ordered = sorted(floors or [], key=lambda f: f.get("order") or 0)
    out: dict[str, dict] = {}
    used: set[int] = set()
    for floor in ordered:
        key = str(floor.get("story_key") or "")
        current = floor.get("elevation_m")
        if not key or current is None:
            continue
        best_i, best_d = None, None
        for i, value in enumerate(seq):
            if i in used:
                continue
            d = abs(value - float(current))
            if d <= tolerance_m and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is not None:
            used.add(best_i)
            out[key] = {
                "elevation_m": round(seq[best_i], 3),
                "delta_m": round(seq[best_i] - float(current), 3),
                "matched": True,
            }
    return out
