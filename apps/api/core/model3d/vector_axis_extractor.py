"""矢量轴线提取:直接读 PDF 矢量线段定轴网,不再栅格化。

**为什么换掉栅格 Hough**:实测这三张专门的轴网定位图
(`A-01-02A 正交轴网 / A-01-03A 中心轴网 / A-01-04A 竖向结构`)里有
**4.1 万 ~ 27.2 万条矢量线段**,坐标精度 0.1pt。先栅格化再 HoughLinesP 等于
把这份精度扔掉,还额外引入像素抖动。

**三个实测得来的关键认知**(决定了本模块的结构):

1. **一张图可以有多套轴网,各自旋转角不同。** A-01-04A 里长度加权主方向是
   `0°/90°`(5.5 万 pt)**和 `43°/133°`(1.8 万 pt)**——后者是一整套旋转 43° 的
   正交轴网。按「单一正交系」假设去读,这套线会被整体当成噪声丢掉。
   故先**聚类主方向对**,再在每个方向系内找轴线。
2. **判据是「覆盖率 + 跨度」双闸。** 图框线覆盖率 1.00,点划线轴线 0.42~0.86,
   噪声(散落短段偶然共线)0.01~0.05,区分度很好。
3. **等间距是真轴网的指纹。** A-01-04A 实测间距序列出现 `68.8, 68.8, 68.9`,
   而噪声间距是 `2.4, 3.0, 3.3`。间距模数可用来给置信度加权。

**边界(如实说)**:本模块只解决**线**。轴号读不出来——实测图面内的轴号是
描边图形而非文字(186 个矢量文字全在图框会签栏),必须人工派号。
"""
from __future__ import annotations

import logging
import math

from core.model3d.axis_normal import normal_offset

logger = logging.getLogger(__name__)

#: 参与朝向投票的最小段长。
#:
#: **曾设 30pt,那是个致命错误**:点划线的长划实测只有 10.5pt,于是虚线轴线
#: 对主方向投票毫无贡献。真图上之所以能测出 0°/90°,靠的是图框与构件的长实线
#: 恰好同方向;而旋转轴网没有长实线撑着,方向就永远找不到——这才是它一直
#: 缺失的主因(不只是法向算错)。
#: 既然投票**按长度加权**,极短段自然权重极小,不必靠长度门槛去噪。
MIN_LENGTH_FOR_DIRECTION_PT = 2.0

#: 主方向聚类容差(度)。CAD 出图角度很准,2° 足够容纳浮点误差
DIRECTION_TOLERANCE_DEG = 2.0

#: 主方向至少要占总长度这个比例才算一套轴网(实测副系统占 18%,故阈值取低)
MIN_DIRECTION_SHARE = 0.03

#: 同一条轴线的法向偏移聚类容差(pt)
POSITION_TOLERANCE_PT = 1.5

#: 双闸:跨度至少占**该方向已见最长线**这个比例 + 覆盖率至少这么高。
#:
#: **不能按整页算**——实测教训:A-01-02A 右上角有一整套旋转 43° 的子轴网,
#: 单条线约 600~900pt,而按整页算的门槛是 3370×0.25=842pt,于是这套轴网
#: 只剩 1 条通过,我据此误判「43° 是斜撑不是轴网」。子轴网只占图面一角,
#: 门槛必须相对**同方向内的最长线**,而不是相对整页。
MIN_ENVELOPE_RATIO = 0.35
MIN_COVERAGE_RATIO = 0.30

#: 图框线排除:位置落在页面最外侧这个比例内的一律不算轴线
FRAME_MARGIN_RATIO = 0.03

#: 间距被认为「同一模数」的相对容差
MODULUS_TOLERANCE = 0.03

#: 模数基准的下限(pt)。轴距不可能只有几个点——实测噪声间距 1.6~5pt,
#: 放它当基准会把规律性算成毫无意义的 0.12。
MIN_MODULUS_BASE_PT = 20.0

#: 一个方向要成为「轴网方向」,必须有单根长度达到页幅这个比例的线。
#:
#: **对点划线不适用**:一条点划线轴线的单根划只有 10.5pt,永远过不了这道闸。
#: 故默认关闭(0.0),把「是不是轴线」交给线型判据去判——那是按国标读,
#: 比拿单根长度猜可靠。仅在明确只想要长实线时才传非零值。
MIN_SINGLE_LINE_RATIO = 0.0


def segment_length(seg: tuple[float, float, float, float]) -> float:
    return math.hypot(seg[2] - seg[0], seg[3] - seg[1])


def segment_angle_deg(seg: tuple[float, float, float, float]) -> float:
    """线段朝向,归一到 [0,180)——轴线无方向性。"""
    dx, dy = seg[2] - seg[0], seg[3] - seg[1]
    if dx == 0 and dy == 0:
        return 0.0
    return math.degrees(math.atan2(dy, dx)) % 180.0


def dominant_directions(
    segments: list[tuple[float, float, float, float]],
    *, tol_deg: float = DIRECTION_TOLERANCE_DEG,
    min_share: float = MIN_DIRECTION_SHARE,
    page_span: float | None = None,
    min_single_line_ratio: float = MIN_SINGLE_LINE_RATIO,
) -> list[dict]:
    """长度加权找主方向。返回 [{angle_deg, length, share, longest}],按总长降序。

    **必须按长度加权**:轴线长而少,构件轮廓短而多。按条数统计会被噪声压过去。

    传了 `page_span` 时再加一道**单根最长线**闸:一个方向即便总长可观,
    若没有一根够长的线,那是构件轮廓而非轴网(实测 42° 方向总长 2 万 pt,
    单根最长却只有 955pt——斜撑,不是轴线)。
    """
    # 角度是**环形量**:179.5° 与 0.5° 只差 1°。分桶键必须对 180 取模,
    # 否则 0° 与 180° 会成两个桶,归一化后又都显示 0°(实测出现过重复方向系)。
    slots = max(int(round(180.0 / tol_deg)), 1)
    buckets: dict[int, float] = {}
    longest: dict[int, float] = {}
    total = 0.0
    for seg in segments:
        length = segment_length(seg)
        if length < MIN_LENGTH_FOR_DIRECTION_PT:
            continue
        key = int(round(segment_angle_deg(seg) / tol_deg)) % slots
        buckets[key] = buckets.get(key, 0.0) + length
        longest[key] = max(longest.get(key, 0.0), length)
        total += length
    if total <= 0:
        return []

    out = []
    for key, length in buckets.items():
        share = length / total
        if share < min_share:
            continue
        if page_span and longest.get(key, 0.0) < page_span * min_single_line_ratio:
            continue          # 没有贯通长线 → 构件轮廓,不是轴网方向
        out.append({"angle_deg": round(key * tol_deg, 2),
                    "length": round(length, 1), "share": round(share, 4),
                    "longest": round(longest.get(key, 0.0), 1)})
    return sorted(out, key=lambda d: -d["length"])


def orthogonal_families(directions: list[dict], tol_deg: float = 3.0) -> list[dict]:
    """把主方向配成**正交对**——一套轴网必然有两族互相垂直的轴线。

    实测 `43°` 与 `133°` 正是同一套旋转 43° 轴网的两族。落单的方向也保留
    (可能是只画了单向轴线的图),但标 `paired=False` 以示证据较弱。
    """
    used: set[int] = set()
    families: list[dict] = []
    for i, a in enumerate(directions):
        if i in used:
            continue
        mate = None
        for j, b in enumerate(directions):
            if j == i or j in used:
                continue
            diff = abs(a["angle_deg"] - b["angle_deg"]) % 180.0
            if abs(min(diff, 180.0 - diff) - 90.0) <= tol_deg:
                mate = (j, b)
                break
        if mate is None:
            families.append({"angles": [a["angle_deg"]], "length": a["length"],
                             "paired": False})
            used.add(i)
            continue
        j, b = mate
        used.update({i, j})
        families.append({"angles": sorted([a["angle_deg"], b["angle_deg"]]),
                         "length": round(a["length"] + b["length"], 1),
                         "paired": True})
    return sorted(families, key=lambda f: -f["length"])


#: 法向偏移的唯一实现在 `axis_normal`——该公式的符号曾错过一次,不再各写一遍
_normal_offset = normal_offset


def _along(x: float, y: float, angle_deg: float) -> float:
    """点沿轴线方向的投影(算跨度用)。"""
    rad = math.radians(angle_deg)
    return x * math.cos(rad) + y * math.sin(rad)


def axes_in_direction(
    segments: list[tuple[float, float, float, float]], angle_deg: float,
    *, page_w: float, page_h: float,
    angle_tol_deg: float = DIRECTION_TOLERANCE_DEG,
    pos_tol_pt: float = POSITION_TOLERANCE_PT,
    min_envelope_ratio: float = MIN_ENVELOPE_RATIO,
    min_coverage_ratio: float = MIN_COVERAGE_RATIO,
    min_zone_members: int = 1,
) -> list[dict]:
    """在指定方向上找轴线:法向偏移聚类 → 跨度/覆盖率双闸。

    `min_zone_members` 是**轴网级**约束(一套轴网至少几条平行线),默认 1 表示
    不约束——本函数只负责「找出该方向上的点划线」,是否成网由 `build_axes` 判。

    返回 [{offset_pt, envelope_pt, coverage, segments}],按 offset 升序。
    """
    picked = []
    for seg in segments:
        diff = abs(segment_angle_deg(seg) - angle_deg) % 180.0
        if min(diff, 180.0 - diff) > angle_tol_deg:
            continue
        if segment_length(seg) < 1.0:
            continue
        mid_off = (_normal_offset(seg[0], seg[1], angle_deg)
                   + _normal_offset(seg[2], seg[3], angle_deg)) / 2
        a1 = _along(seg[0], seg[1], angle_deg)
        a2 = _along(seg[2], seg[3], angle_deg)
        picked.append((mid_off, min(a1, a2), max(a1, a2)))
    if not picked:
        return []

    groups: list[dict] = []
    for off, lo, hi in sorted(picked):
        if groups and off - groups[-1]["offset_pt"] <= pos_tol_pt:
            g = groups[-1]
            g["coverage_pt"] += hi - lo
            g["lo"] = min(g["lo"], lo)
            g["hi"] = max(g["hi"], hi)
            g["n"] += 1
            g["offset_pt"] = (g["offset_pt"] * (g["n"] - 1) + off) / g["n"]
            g["spans"].append((lo, hi))
        else:
            groups.append({"offset_pt": off, "coverage_pt": hi - lo,
                           "lo": lo, "hi": hi, "n": 1, "spans": [(lo, hi)]})

    # **主筛是线型,不是覆盖率**。GB/T 50001 §8.0.1:定位轴线用单点长画线。
    #
    # 实测踩过的坑:只用「覆盖率 ≥0.3」筛,竖向检出 187 条(真值 36)——因为
    # **覆盖率 1.0 恰恰是实线**(构件轮廓/图框),而轴线是点划线、覆盖率在
    # 0.3~0.9。用「覆盖率越高越像轴线」的直觉筛,方向正好是反的。
    # 改按划-空节奏判线型,只留单点长画线,是按制图标准读而非调阈值。
    from core.model3d.line_type_classifier import DASH_DOT, classify_spans

    solid = []
    for g in groups:
        envelope = g["hi"] - g["lo"]
        if envelope <= 0:
            continue
        if classify_spans(g["spans"])["line_type"] != DASH_DOT:
            continue
        solid.append(g)

    # **门槛按分区各自算**。GB/T 50001 §8.0.5 允许分区编号,不同分区是独立
    # 坐标系、尺度不同;全图统一门槛会把尺度小的那套整批砍掉。
    out = []
    for zone in _cluster_zones(solid):
        if len(zone) < min_zone_members:
            continue          # 轴网级约束:孤线不成网(由 build_axes 传入)
        zone_longest = max(g["hi"] - g["lo"] for g in zone)
        floor_env = zone_longest * min_envelope_ratio
        for g in zone:
            envelope = g["hi"] - g["lo"]
            if envelope < floor_env:
                continue
            coverage = min(g["coverage_pt"] / envelope, 1.0)
            out.append({
                "offset_pt": round(g["offset_pt"], 2),
                "envelope_pt": round(envelope, 2),
                "coverage": round(coverage, 3),
                "segments": g["n"],
                "along_lo": round(g["lo"], 2), "along_hi": round(g["hi"], 2),
            })
    return sorted(out, key=lambda a: a["offset_pt"])


#: 两条轴线算「同一分区」的沿线区间端点容差(相对区间长度)。
#:
#: **判据必须是「区间相似」而不是「区间重叠」**。实测教训:分区 1 的轴线区间
#: 是 [1586,2165],另一区是 [650,2165] —— 前者完全**嵌套**在后者内,重叠率 100%,
#: 按重叠聚类根本分不开(改完召回率一点没动)。真正的信号是同一套轴网被画到
#: 相同范围,故两端点都要接近。
ZONE_ENDPOINT_TOLERANCE = 0.15

#: 一套轴网至少要有几条平行轴线。孤零零一条不成网,多半是构件边线。
MIN_ZONE_MEMBERS = 3


def _cluster_zones(
    groups: list[dict], tol: float = ZONE_ENDPOINT_TOLERANCE,
) -> list[list[dict]]:
    """按沿线区间**相似度**把共线组分成若干分区。

    同一分区的轴线画到相同范围(两端点都接近);不同分区范围不同。
    """
    if not groups:
        return []
    zones: list[list[dict]] = []
    for g in sorted(groups, key=lambda g: (g["lo"], g["hi"])):
        length = max(g["hi"] - g["lo"], 1.0)
        placed = False
        for zone in zones:
            ref = zone[0]
            if (abs(ref["lo"] - g["lo"]) <= length * tol
                    and abs(ref["hi"] - g["hi"]) <= length * tol):
                zone.append(g)
                placed = True
                break
        if not placed:
            zones.append([g])
    return zones


def modulus_score(
    offsets: list[float], tol: float = MODULUS_TOLERANCE,
    min_base_pt: float = MIN_MODULUS_BASE_PT,
) -> dict:
    """间距规律性打分——**等间距是真轴网的指纹**。

    实测真柱网间距 `68.8, 68.8, 68.9`,噪声 `2.4, 3.0, 3.3`。

    **base 取「间距众数」而不是「整数倍命中最多的候选」**:后者总会选中最小间距
    (1.6pt 这种噪声的整数倍能命中一切),实测因此把规律性算成 0.12——毫无意义。
    众数才对应真实柱距。`min_base_pt` 再挡掉一层:轴距不可能只有几个点。
    """
    if len(offsets) < 3:
        return {"base": None, "ratio": 0.0}
    spacings = [b - a for a, b in zip(sorted(offsets), sorted(offsets)[1:])]
    spacings = [s for s in spacings if s >= min_base_pt]
    if not spacings:
        return {"base": None, "ratio": 0.0}

    # 众数:按相对容差聚类,取最大簇的均值
    best_base, best_hits = None, 0
    for cand in spacings:
        group = [s for s in spacings if abs(s / cand - 1.0) <= tol]
        if len(group) > best_hits:
            best_base, best_hits = sum(group) / len(group), len(group)
    if best_base is None:
        return {"base": None, "ratio": 0.0}
    # ratio 按「符合该模数或其整数倍」统计,容许轴网跳号
    hits = sum(1 for s in spacings
               if abs(s / best_base - round(s / best_base)) <= tol
               and round(s / best_base) >= 1)
    return {"base": round(best_base, 2),
            "ratio": round(hits / len(spacings), 3)}


def to_normalized_line(axis: dict, angle_deg: float, page_h: float) -> dict:
    """轴线 → 归一化端点(同除页高,与全链路口径一致)。"""
    rad = math.radians(angle_deg)
    nx, ny = -math.sin(rad), math.cos(rad)      # 与 _normal_offset 同一法向
    dx, dy = math.cos(rad), math.sin(rad)
    off = axis["offset_pt"]
    base_x, base_y = off * nx, off * ny
    lo, hi = axis["along_lo"], axis["along_hi"]
    return {
        "x1_norm": round((base_x + dx * lo) / page_h, 6),
        "y1_norm": round((base_y + dy * lo) / page_h, 6),
        "x2_norm": round((base_x + dx * hi) / page_h, 6),
        "y2_norm": round((base_y + dy * hi) / page_h, 6),
    }


def _is_frame(axis: dict, angle_deg: float, page_w: float, page_h: float) -> bool:
    """图框线判定:位置贴着页边 + 覆盖率接近满(实测图框覆盖率 1.00)。

    法向偏移带符号(见 `_normal_offset`),故按**绝对值**与页幅比。
    """
    span = page_w if abs(math.sin(math.radians(angle_deg))) > 0.7 else page_h
    margin = span * FRAME_MARGIN_RATIO
    off = abs(axis["offset_pt"])
    near_edge = off < margin or off > span - margin
    return near_edge and axis["coverage"] > 0.95


def build_axes(
    segments: list[tuple[float, float, float, float]],
    page_w: float, page_h: float, *, max_families: int = 3,
) -> dict:
    """矢量线段 → 分方向系的轴线候选(纯计算,可离线测)。

    返回 {families: [{angles, axes: [...], modulus}], directions: [...]}。
    """
    directions = dominant_directions(
        segments, page_span=max(page_w, page_h))
    families = orthogonal_families(directions)[:max_families]
    out_families = []
    for fam in families:
        fam_axes = []
        for angle in fam["angles"]:
            found = axes_in_direction(
                segments, angle, page_w=page_w, page_h=page_h,
                min_zone_members=MIN_ZONE_MEMBERS)
            found = [a for a in found if not _is_frame(a, angle, page_w, page_h)]
            mod = modulus_score([a["offset_pt"] for a in found])
            fam_axes.append({
                "angle_deg": angle, "count": len(found),
                "modulus": mod,
                "axes": [{**a, **to_normalized_line(a, angle, page_h),
                          "direction": _direction_key(angle)} for a in found],
            })
        out_families.append({"angles": fam["angles"], "paired": fam["paired"],
                             "length": fam["length"], "groups": fam_axes})
    return {"directions": directions, "families": out_families}


def _direction_key(angle_deg: float) -> str:
    """朝向 → 与既有 manual_axis 一致的 direction 取值。"""
    from services.axis_geometry import orientation
    return orientation({"x1_norm": 0.0, "y1_norm": 0.0,
                        "x2_norm": math.cos(math.radians(angle_deg)),
                        "y2_norm": math.sin(math.radians(angle_deg))})


# ── IO 层(优雅降级)──────────────────────────────────────────────

def segments_from_pdf(pdf_bytes: bytes) -> tuple[list, float, float]:
    """PDF 首页矢量线段 + 页面尺寸。依赖缺失/异常 → ([], 0, 0)。

    矩形按四条边展开——CAD 出图里图框与部分轴线是矩形图元。
    """
    try:
        import fitz

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if doc.page_count < 1:
                return [], 0.0, 0.0
            page = doc[0]
            segs: list[tuple[float, float, float, float]] = []
            for path in page.get_drawings():
                for item in path.get("items") or []:
                    if item[0] == "l":
                        segs.append((item[1].x, item[1].y, item[2].x, item[2].y))
                    elif item[0] == "re":
                        r = item[1]
                        x0, y0, x1, y1 = r.x0, r.y0, r.x1, r.y1
                        segs += [(x0, y0, x1, y0), (x1, y0, x1, y1),
                                 (x1, y1, x0, y1), (x0, y1, x0, y0)]
            return segs, float(page.rect.width), float(page.rect.height)
    except Exception as exc:  # noqa: BLE001 — 取不到矢量就退回栅格检测
        logger.warning("[vector_axis] 矢量线段提取失败: %s", exc)
        return [], 0.0, 0.0


def extract_vector_axes(pdf_bytes: bytes, *, max_families: int = 3) -> dict:
    """PDF → 分方向系的轴线候选。无矢量内容返回空结构(调用方退回栅格)。"""
    segments, page_w, page_h = segments_from_pdf(pdf_bytes)
    if not segments or page_h <= 0:
        return {"directions": [], "families": [], "segments": 0}
    result = build_axes(segments, page_w, page_h, max_families=max_families)
    result["segments"] = len(segments)
    return result
