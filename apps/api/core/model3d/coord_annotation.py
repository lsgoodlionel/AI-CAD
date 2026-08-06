"""坐标标注识别:找引线、读坐标值、修丢失的负号。

**图面结构**(渲图确认 + 实测):`文字 → 水平段 → 斜段 → 末端落在轴线交叉点上`。
A-01-02A 实测检出 **16 处**引线,末端到轴线的法向距离 0.03~1.47pt,
水平段长度高度一致(93.7pt × 12 处)。

**为什么坐标必须读而不能推**:轴号有编写顺序可推(GB/T 50001 §8.0.3),
坐标值是任意实数。所幸坐标文字比轴号大一个量级,**OCR 实测置信 0.96~1.00
且逐字符正确**——同一套 RapidOCR 在轴号上只有 1/24,差别就在字号:

    X=-6084.141 / Y=23.524      ✓
    X=-6164.580 / Y=-179.651    ✓
    X=-6228.501 / Y=-156.750    ✓
    X= 6005.463 / 109.401       ← **负号丢了**

**负号是致命项**:符号错会把模型挪到 12 公里外。两重一致性修复:

1. 本模块 `repair_sign_by_consensus`——同图坐标聚成一簇(实测 X∈[-6229,-5922]),
   与簇同量级但符号相反的孤立值只能是丢号;**量级不同就不动**(猜错比不修更糟)。
2. `services.drawing_anchor` 拟合相似变换后按残差复核——符号错的点残差会爆掉。

坐标标注给出的是 **页面位置 ↔ 工程坐标** 的锚点对,是把图纸放进真实世界的钥匙。
"""
from __future__ import annotations

import math
import re

from core.model3d.axis_normal import normal_offset

#: 判为「水平段」的角度容差(度)。引线水平段是严格水平的
HORIZONTAL_TOLERANCE_DEG = 1.0

#: 斜段的角度区间(度,mod 180)。排掉水平与竖直
DIAGONAL_MIN_DEG = 8.0
DIAGONAL_MAX_DEG = 82.0

#: 两段相接的端点容差(pt)。必须紧——松了会把无关线段凑成引线
JOINT_TOLERANCE_PT = 2.0

#: 引线段长度区间(pt)。实测水平段 93.7~107.2、斜段 53.7~112.1
MIN_LEADER_SEGMENT_PT = 30.0
MAX_LEADER_SEGMENT_PT = 400.0

#: 斜段末端到轴线的最大法向距离(pt)。实测 0.03~1.47
TIP_TO_AXIS_TOLERANCE_PT = 3.0

#: 末端去重的网格(pt)
TIP_DEDUPE_PT = 1.0

#: 符号修复:被判为「同一簇」的量级相对容差
MAGNITUDE_BAND_RATIO = 0.25

#: 符号修复所需的多数占比。低于此值不动手
SIGN_MAJORITY_RATIO = 0.7

#: 文字裁图窗口相对水平段长度的左右外扩比例。文字大致与水平段等宽,
#: 留 20% 余量兜住首尾字符
CROP_MARGIN_RATIO = 0.20

#: 裁图窗口的半高 = 水平段长 × 该比例。坐标是 `X=` / `Y=` 两行,
#: 实测水平段 93.7pt 时 ±40pt 刚好,即 0.43
CROP_HALF_HEIGHT_RATIO = 0.43

#: 裁图窗口的最小半宽/半高(pt),防止极短引线裁出空图
MIN_CROP_HALF_PT = 12.0

_COORD_RE = re.compile(r"([XY])\s*=\s*(-?\s*\d+(?:\.\d+)?)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"-?\s*\d+(?:\.\d+)?")

#: 裸数字要被当成坐标,**必须带小数**。实测坐标一律 3 位小数
#: (-6084.141 / 23.524),而尺寸标注是整数(2900 / 4000 / 10200)——
#: A-01-04A 正是因为没这条判据,把 `(10200, 5361)` 当成了坐标。
_DECIMAL_NUMBER_RE = re.compile(r"-?\s*\d+\.\d+")


def _angle(seg) -> float:
    return math.degrees(math.atan2(seg[3] - seg[1], seg[2] - seg[0])) % 180.0


def _length(seg) -> float:
    return math.dist((seg[0], seg[1]), (seg[2], seg[3]))


def _is_horizontal(seg) -> bool:
    a = _angle(seg)
    return min(a, 180.0 - a) < HORIZONTAL_TOLERANCE_DEG


def _is_diagonal(seg) -> bool:
    a = _angle(seg)
    return (DIAGONAL_MIN_DEG < a < DIAGONAL_MAX_DEG
            or 180.0 - DIAGONAL_MAX_DEG < a < 180.0 - DIAGONAL_MIN_DEG)


def _ends(seg) -> tuple[tuple[float, float], tuple[float, float]]:
    return (seg[0], seg[1]), (seg[2], seg[3])


def _distance_to_nearest_axis(point, axes: list[dict]) -> float:
    if not axes:
        return float("inf")
    return min(abs(normal_offset(point[0], point[1], a["angle_deg"]) - a["offset_pt"])
               for a in axes)


def find_leaders(segments: list[tuple], axes: list[dict],
                 tip_tol: float = TIP_TO_AXIS_TOLERANCE_PT) -> list[dict]:
    """线段 + 轴线 → 坐标标注引线。

    判据:**一段水平 + 一段斜向、端点相接、斜段远端落在某条轴线上**。
    返回 [{text_anchor, joint, tip, horizontal_len, diagonal_len, tip_axis_gap}]，
    `text_anchor` 是水平段的远端——坐标文字就写在它附近。
    """
    sized = [s for s in segments
             if MIN_LEADER_SEGMENT_PT <= _length(s) <= MAX_LEADER_SEGMENT_PT]
    horizontals = [s for s in sized if _is_horizontal(s)]
    diagonals = [s for s in sized if _is_diagonal(s)]

    found: dict[tuple[int, int], dict] = {}
    for h in horizontals:
        for d in diagonals:
            for hi, h_end in enumerate(_ends(h)):
                for di, d_end in enumerate(_ends(d)):
                    if math.dist(h_end, d_end) > JOINT_TOLERANCE_PT:
                        continue
                    tip = _ends(d)[1 - di]
                    gap = _distance_to_nearest_axis(tip, axes)
                    if gap > tip_tol:
                        continue
                    key = (round(tip[0] / TIP_DEDUPE_PT),
                           round(tip[1] / TIP_DEDUPE_PT))
                    record = {
                        "text_anchor": _ends(h)[1 - hi],
                        "joint": d_end,
                        "tip": tip,
                        "horizontal_len": round(_length(h), 2),
                        "diagonal_len": round(_length(d), 2),
                        "tip_axis_gap": round(gap, 3),
                    }
                    # 同一末端保留轴线贴合最好的那条
                    if key not in found or gap < found[key]["tip_axis_gap"]:
                        found[key] = record
    return sorted(found.values(), key=lambda r: (r["tip"][1], r["tip"][0]))


def parse_coordinate_tokens(tokens: list[str]) -> dict | None:
    """OCR token → {x, y}。容忍 `X= -6228.501` 这类空格,也容忍丢了标签的裸数字。

    实测 OCR 会把 `Y=-109.401` 读成 `109.401`(标签与符号一起丢),
    所以带标签的先认,剩下的裸数字按顺序补空缺。
    """
    labelled: dict[str, float] = {}
    bare: list[float] = []
    for token in tokens or []:
        text = (token or "").strip()
        matched = False
        for label, value in _COORD_RE.findall(text):
            number = float(value.replace(" ", ""))
            key = label.upper()
            if key in labelled and labelled[key] != number:
                # 一个窗口里出现两个不同的 X(或 Y)= 裁图框进了邻近标注,
                # 取第一个就是猜。实测 A-01-04A 因此产生大量粗错。
                return None
            labelled[key] = number
            matched = True
        if not matched:
            # 裸数字必须带小数才可能是坐标;整数是尺寸标注
            for value in _DECIMAL_NUMBER_RE.findall(text):
                bare.append(float(value.replace(" ", "")))

    for axis in ("X", "Y"):
        if axis not in labelled and bare:
            labelled[axis] = bare.pop(0)
    if "X" not in labelled or "Y" not in labelled:
        return None
    return {"x": labelled["X"], "y": labelled["Y"]}


def text_crop_rect(leader: dict) -> tuple[float, float, float, float]:
    """引线 → 坐标文字的裁图矩形 `(x0, y0, x1, y1)`(页面 pt)。

    **窗口必须随引线尺度变化**:实测三张图的水平段是 93.7 / 58.3 / 33.4pt
    三个量级。用固定的 ±130pt 窗,A-01-04A 一次框进 2~3 处标注,
    OCR 在一个窗口里读出两个 X 值 —— 这是它 20 条引线只有 6 条内点的主因。

    文字写在水平段上方/下方且与之大致等宽,所以窗口取该段的包围盒左右外扩。
    """
    ax, ay = leader["text_anchor"]
    jx, jy = leader.get("joint", (ax, ay))
    length = float(leader.get("horizontal_len") or abs(jx - ax)) or 1.0
    margin = max(length * CROP_MARGIN_RATIO, MIN_CROP_HALF_PT)
    half_h = max(length * CROP_HALF_HEIGHT_RATIO, MIN_CROP_HALF_PT)
    x0, x1 = min(ax, jx) - margin, max(ax, jx) + margin
    cy = (ay + jy) / 2
    return (x0, cy - half_h, x1, cy + half_h)


def repair_sign_by_consensus(values: list[float],
                             band: float = MAGNITUDE_BAND_RATIO,
                             majority: float = SIGN_MAJORITY_RATIO) -> list[float]:
    """修复被 OCR 丢掉的负号(不改入参)。

    只在**符号有明显多数**且该值**与多数同量级**时才翻转:
    - 实测 X∈[-6229,-5922],孤立的 +6005.463 必是丢号 → 修;
    - 实测 Y 本来有正有负(-179.651 ~ +47.504)→ 不动;
    - 量级完全不同(如 12.5 混在 -6000 里)→ 不动,翻符号也无意义。
    """
    if len(values) < 3:
        return list(values)

    negatives = [v for v in values if v < 0]
    positives = [v for v in values if v > 0]
    if len(negatives) >= majority * len(values):
        crowd, wrong_sign = negatives, 1
    elif len(positives) >= majority * len(values):
        crowd, wrong_sign = positives, -1
    else:
        return list(values)          # 符号本就混杂,不动手

    reference = sum(abs(v) for v in crowd) / len(crowd)
    out = []
    for value in values:
        same_magnitude = abs(abs(value) - reference) <= band * reference
        out.append(-value if (value * wrong_sign > 0 and same_magnitude) else value)
    return out


# ── RANSAC 定变换(最小二乘对粗差无免疫)────────────────────────────

#: 判为内点的残差上限(米)。实测尺度 1pt ≈ 0.12m,2m 已很宽松
INLIER_TOLERANCE_M = 2.0

#: 解一个相似变换所需的最少点对
MIN_PAIRS_FOR_MODEL = 2


def _similarity_from_two(p0: dict, p1: dict) -> dict | None:
    """两对点解相似变换 page → world(缩放 + 旋转 + 平移)。"""
    (ax, ay), (bx, by) = p0["page"], p1["page"]
    (cx, cy), (dx, dy) = p0["world"], p1["world"]
    sx, sy = bx - ax, by - ay
    tx_, ty_ = dx - cx, dy - cy
    den = sx * sx + sy * sy
    if den <= 1e-12:
        return None
    # 复数除法:(t)/(s) 同时给出尺度与旋转
    cos_s = (sx * tx_ + sy * ty_) / den
    sin_s = (sx * ty_ - sy * tx_) / den
    scale = math.hypot(cos_s, sin_s)
    if scale <= 1e-12:
        return None
    return {
        "scale": scale,
        "rotation_deg": math.degrees(math.atan2(sin_s, cos_s)),
        "tx": cx - (cos_s * ax - sin_s * ay),
        "ty": cy - (sin_s * ax + cos_s * ay),
    }


def _project(transform: dict, page: tuple[float, float]) -> tuple[float, float]:
    theta = math.radians(transform["rotation_deg"])
    s = transform["scale"]
    cos_t, sin_t = math.cos(theta) * s, math.sin(theta) * s
    x, y = page
    return (cos_t * x - sin_t * y + transform["tx"],
            sin_t * x + cos_t * y + transform["ty"])


def _residual(transform: dict, pair: dict) -> float:
    px, py = _project(transform, pair["page"])
    return math.dist((px, py), pair["world"])


def ransac_similarity(pairs: list[dict],
                      tol: float = INLIER_TOLERANCE_M) -> dict | None:
    """页面↔工程坐标点对 → 相似变换 + 内外点划分。

    **为什么不用最小二乘**:实测 16 个坐标标注里有 3 个粗错(OCR 丢负号 + 一处
    误读),占 19%。最小二乘被整体拽偏后 RMSE 达 72m、残差 9~183m **没有分界**,
    判别力完全失效。RANSAC 用两点定模型、按内点数投票,对粗差免疫。

    **穷举点对而非随机采样**:点数少(实测 16),穷举 120 个组合即可,
    而且**结果可复现**——随机采样会让同一输入给出不同答案,无法回归测试。

    返回 {transform, inliers, outliers, rmse};点对不足返回 None。
    """
    n = len(pairs)
    if n < MIN_PAIRS_FOR_MODEL + 1:
        return None

    best = None
    for i in range(n):
        for j in range(i + 1, n):
            model = _similarity_from_two(pairs[i], pairs[j])
            if model is None:
                continue
            inliers = [k for k in range(n) if _residual(model, pairs[k]) <= tol]
            if len(inliers) < MIN_PAIRS_FOR_MODEL:
                continue
            rmse = math.sqrt(sum(_residual(model, pairs[k]) ** 2
                                 for k in inliers) / len(inliers))
            # 内点多者优先;并列取 rmse 小者,再并列取下标小者(保证确定性)
            key = (-len(inliers), round(rmse, 9), i, j)
            if best is None or key < best[0]:
                best = (key, model, inliers, rmse)

    if best is None:
        return None
    _key, model, inliers, rmse = best
    return {
        "transform": {k: round(v, 9) for k, v in model.items()},
        "inliers": inliers,
        "outliers": [k for k in range(n) if k not in set(inliers)],
        "rmse": round(rmse, 6),
    }


def repair_outliers_by_transform(pairs: list[dict], fit: dict | None,
                                 tol: float = INLIER_TOLERANCE_M) -> list[dict]:
    """按已定的变换修外点的符号(不改入参)。

    Y 值本来正负混杂,`repair_sign_by_consensus` 按设计不敢动它——但**变换能判**:
    翻一下符号若残差落回容差内,就是 OCR 丢了负号。

    修不了的外点**明确标出**(`outlier=True, repaired=None`)交给人工,
    绝不悄悄留在锚点里——错的世界坐标比缺一个锚点危险得多。
    """
    out = [{**p, "outlier": False, "repaired": None} for p in pairs]
    if not fit:
        return out
    transform = fit["transform"]
    for k in fit["outliers"]:
        wx, wy = pairs[k]["world"]
        for label, candidate in (("x_sign", (-wx, wy)),
                                 ("y_sign", (wx, -wy)),
                                 ("xy_sign", (-wx, -wy))):
            if _residual(transform, {"page": pairs[k]["page"],
                                     "world": candidate}) <= tol:
                out[k] = {**pairs[k], "world": candidate,
                          "outlier": False, "repaired": label}
                break
        else:
            out[k] = {**pairs[k], "outlier": True, "repaired": None}
    return out
