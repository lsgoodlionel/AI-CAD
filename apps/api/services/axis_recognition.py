"""轴网识别编排:把 Phase I 各块串成一条可交付的链路。

链路:**圈 → 带 → 分区 → 轴号 → 引线 → OCR → 符号修复 → RANSAC → 轴号对锚点**。

**为什么要有这一层**:各块此前只在一次性脚本里串起来跑通过,结果没有落点——
分区号靠脚本硬编、粗错只打印在 stdout。要进产品就得有一个可测、可重跑、
并把**三样必须人工过目的东西**如实交出来的编排:

1. **分区编号**(§8.0.5 未规定哪个分区是 1,几何推不出)→ `zones[].zone_label`
   为 None 时表示待确认,**绝不瞎猜一个**;
2. **粗错坐标**(RANSAC 判出的 OCR 误读)→ `outliers`,不写进锚点;
3. **国标校验违规**(§8.0.3~8.0.6)→ `violations`。

**可测性**:OCR 由调用方注入(`read_text`),整条链在离线环境完全可测,
不依赖 RapidOCR 是否装得上;OCR 抛异常也只降级坐标部分,不拖垮轴线识别。
"""
from __future__ import annotations

from typing import Any

import logging

from core.model3d.axis_label_band import bands_to_axes, detect_bands
from core.model3d.axis_label_derive import derive_zone_labels
from core.model3d.axis_label_glyph import mark_fraction_circles
from core.model3d.axis_zone_grouping import (
    attach_small_bands, group_bands_into_zones, zone_extent,
)
from core.model3d.coord_annotation import (
    find_leaders, parse_coordinate_tokens, ransac_similarity,
    repair_outliers_by_transform, repair_sign_by_consensus,
)
from core.model3d.drawing_conventions import validate_axis_labels
from services.axis_world_anchors import anchor_records, transform_from_axes
from services.multi_view_split import (
    SPLIT_VIEW_WARNING, is_split_view, renumber_split_views,
)

logger = logging.getLogger(__name__)

#: 分区编号未确认时的取值。**语义是「等人工」,不是「没有」**
ZONE_LABEL_PENDING = None

#: 参与检测的主方向。0/90 是正交,42/132 是实测存在的旋转分区
DEFAULT_DIRECTIONS = (0.0, 42.0, 90.0, 132.0)

#: 设备符号场的**提示**阈值(不是拦截阈值)。
#:
#: 上一版在带数 > 40 时**直接不产出轴线**,结果误杀了
#: `A-10-04C 一层完整平面图`——它有 **42 条带**,只超 2 条,整层轴线全丢,
#: 而它是全项目最核心的一张平面图。
#:
#: 三条替代判据全部被实测证伪:
#:
#: | 判据 | 一层平面 | 地下二层 | 轴网定位 | 喷淋 P-21-09C |
#: |---|---|---|---|---|
#: | 带数 | **42** | 13 | 9 | 200 |
#: | 最长带 | 22 | 24 | 24 | **58**(喷淋反而更长) |
#: | 圈内有笔画(§8.0.2) | 100% | 100% | 100% | **100%**(分不开) |
#: | 圈数 | 285 | 112 | 108 | **2340** |
#:
#: **既然分不开,就不能让判错的代价是丢掉整层轴线。** 改为照常产出 +
#: 打 `suspect_symbol_field` 标记,由消费方(入 3D 场景、写世界锚点)跳过。
#: 失败模式从「漏掉真轴网」变成「标记不准」——后者可查、可改、不丢数据。
#:
#: 取 60:实测真轴网上限 42、喷淋 200,离两边都远。
#: **不能改用直径判**:轴号圈实测 5.65~9.88mm、喷头 2.50~2.60mm 看似可分,
#: 但抽样 40 张后发现 A-01-04A 恰好落在 5.65mm —— 阈值定高杀真图、定低放噪声。
SYMBOL_FIELD_BAND_HINT = 60


#: 非几何图被拦下时写进 `warnings` 的说明。
#: **降级必须可见**：置零而不跳过，界面上才能与「还没跑」分开。
NON_GEOMETRIC_WARNING = (
    "本图为**系统图/原理图/接线图**一类的示意图，**不表达平面位置**，"
    "因此不产出定位轴线 —— 定位轴线用于平面定位（GB/T 50001 §8）。"
    "实测「消火栓系统原理图」曾被识别出 385 条轴线、21 个分区"
    "（那些「圈」是管道节点符号，分区数也远超工程常识）。"
)


def should_skip_axes(drawing: Any) -> bool:
    """这张图是否**本就不该有轴网**（非几何示意图）。

    判据复用 `drawing_role` 的 `ROLE_NON_GEOMETRIC`（国标术语，
    不绑任何院的编号体系）—— 判据早就有，识别层此前没去读它。

    **只拦非几何图**：立面/剖面有单向轴线（Phase I 靠它们做 z 恢复）、
    详图也可能带轴号（§9.4.4），都要放行。判不出就放行 ——
    宁可多识别，不可漏掉真轴网图。
    """
    if not drawing:
        return False
    try:
        from services.drawing_role import ROLE_NON_GEOMETRIC, classify_role

        return classify_role(drawing).role == ROLE_NON_GEOMETRIC
    except Exception:  # noqa: BLE001 — 判不了就放行，不阻断识别
        return False


#: 轴距小于此值（米）即不可能是定位轴线。
#:
#: GB/T 50001 §8 的定位轴线用于**主要承重构件**定位，间距是柱网尺度。
#: 实测误检图的「轴距」是 0.29~1.45 米 —— 那是桩位/设备符号被当成轴号圈，
#: 而同批图上要求「≥60% 页幅长直线」的旧启发式**一条轴线都没检出**，
#: 印证这些图本就没有贯通轴网。
#:
#: **必须用米而非 pt**：真值图 A-01-04A 约 4.9 米/轴距、误检图 0.29~1.45 米，
#: 米能分开；而 pt 间距反倒是真值图更密（99 条 34pt vs 误检 66 条 51pt）。
MIN_PLAUSIBLE_AXIS_GAP_M = 2.0


def is_suspect_symbol_field(band_count: int,
                            gap_m: float | None = None) -> bool:
    """带数是否多到疑为设备符号场；轴距过密同样可疑。

    抽成纯函数,是为了让判据本身可直接测——用合成圆圈去凑出 60+ 条带
    会跟贪心分带算法较劲(错开的行会形成近竖向链被整条吸走),
    测出来的是分带算法而不是这条判据。
    """
    if band_count > SYMBOL_FIELD_BAND_HINT:
        return True
    # 轴距过密 —— 带数判据漏掉的那一类（实测 183 张假轴网）。
    # 算不出米轴距时不猜，只用带数判。
    return bool(gap_m and 0 < gap_m < MIN_PLAUSIBLE_AXIS_GAP_M)

#: 一个分区至少要有一对**互相垂直**的带(横行标注竖向轴线、竖列标注横向轴线)。
#: **此前这里用「成员数 ≥ 8」当主带门槛,那是从验证脚本带过来的魔数**——
#: 小图纸的分区可能只有五六条轴线,会被整个漏掉。改用「配上对没有」作判据:
#: 配上对的成带成区,落单的再按分区二维范围吸附。
MIN_BANDS_PER_ZONE = 2


def recognize(circles: list[dict], *, strokes: list[tuple],
              segments: list[tuple], page_w: float, page_h: float,
              read_text, zone_labels: dict[int, str] | None = None,
              directions: tuple[float, ...] = DEFAULT_DIRECTIONS,
              scale_m_pt: float | None = None,
              circle_candidates: list[dict] | None = None) -> dict:
    """一次完整识别。`read_text(leader)` 返回该引线文字处的 OCR token 列表。

    传整条引线而非仅锚点:裁图窗口要按**引线尺度**算(见 `text_crop_rect`),
    固定窗口在引线短的图上会框进邻近标注。

    `zone_labels` 是已由人工确认的 {分区下标: 分区号};未确认的留 None。
    """
    warnings: list[str] = []
    zone_labels = zone_labels or {}
    result = _empty(page_w, page_h, len(circles))
    if not circles or page_h <= 0:
        return result

    marked = mark_fraction_circles(strokes, circles)
    main = [c for c in marked if not c["is_additional"]]
    result["additional_count"] = len(marked) - len(main)

    bands = detect_bands(main, directions=directions)
    if is_suspect_symbol_field(len(bands)):
        # **只标记,不拦截**(见 SYMBOL_FIELD_BAND_HINT 注释):
        # 无法可靠区分设备符号场与大型平面图,所以照常产出轴线,
        # 由消费方按此标记决定要不要用。
        result["suspect_symbol_field"] = True
        # **必须写进 `warnings` 这个局部列表**,不能写 result["warnings"]——
        # 函数末尾是 `result["warnings"] = warnings`(整体赋值),
        # 写进 result 的会被原样覆盖掉。
        warnings.append(
            f"检出 {len(bands)} 条带,超过提示阈值 {SYMBOL_FIELD_BAND_HINT} —— "
            f"疑为设备符号场(如喷淋平面图满图喷头)。轴线照常产出并留档,"
            f"但不进入 3D 场景与世界锚点,待人工确认")
    grouped = group_bands_into_zones(bands)
    paired = [z for z in grouped if len(z["bands"]) >= MIN_BANDS_PER_ZONE]
    lonely = [b for z in grouped if len(z["bands"]) < MIN_BANDS_PER_ZONE
              for b in z["bands"]]
    zones = attach_small_bands(paired, lonely) if paired else grouped

    # §8.0.5 的分区编号**只在多分区时才用**。单分区图的轴号 `1` 就是 `1`,
    # 不存在 `1-1` vs `2-1` 撞身份,人工确认无信息可加 —— 再要求确认就是白等。
    needs_confirmation = len(zones) > 1

    labelled: list[dict] = []
    zone_records: list[dict] = []
    for index, zone in enumerate(zones):
        label = zone_labels.get(index, ZONE_LABEL_PENDING)
        _zone_axes = bands_to_axes(zone["bands"], page_h=page_h)
        if circle_candidates is not None and _zone_axes:
            from core.model3d.axis_label_circle import normal_offset
            _ang = float(_zone_axes[0].get("angle_deg") or 0.0)
            _zone_axes = recover_gap_axes(
                _zone_axes,
                [normal_offset(c["cx"], c["cy"], _ang) for c in circle_candidates])
        axes = derive_zone_labels(_zone_axes, zone=label)
        confirmed = (not needs_confirmation) or label is not ZONE_LABEL_PENDING
        for axis in axes:
            axis["zone_index"] = index
            axis["zone_label_confirmed"] = confirmed
        labelled.extend(axes)
        x0, y0, x1, y1 = zone_extent(zone)
        zone_records.append({
            "index": index,
            "zone_label": label,
            "needs_confirmation": needs_confirmation and label is ZONE_LABEL_PENDING,
            "numeric_axes": sum(1 for a in axes if a["label_kind"] == "numeric"),
            "alpha_axes": sum(1 for a in axes if a["label_kind"] == "alpha"),
            "extent": [round(v, 2) for v in (x0, y0, x1, y1)],
        })
        result["violations"].extend(
            _zone_violations(index, axes))

    # §8.0.2 两端各注一个轴号 → 同一条轴线不能算两条
    labelled = merge_both_end_labels(labelled)

    # **合并之后再查**：两端重注的镜像分区各查一遍会把同一处报两次
    from collections import defaultdict as _dd
    _by_zone: dict = _dd(list)
    for _axis in labelled:
        _by_zone[_axis.get("zone_index")].append(_axis)
    for _zone, _axes in _by_zone.items():
        _angle = float((_axes[0].get("angle_deg") or 0.0)) if _axes else 0.0
        _offs = None
        if circle_candidates is not None:
            from core.model3d.axis_label_circle import normal_offset
            _offs = [normal_offset(c["cx"], c["cy"], _angle)
                     for c in circle_candidates]
        for flag in suspect_missing_axis_gaps(_axes, circle_offsets=_offs):
            result["violations"].append({
                "code": "suspect_missing_axis",
                "zone": _zone,
                "message": (
                    f"轴号 {flag['after_label']} 之后的间距是其余档的 "
                    f"{flag['multiple']} 倍，"
                    + ("**缺口处有轴号圈，确认漏检**"
                       if flag.get("confirmed") else
                       ("缺口处无圈，多半是不等跨" if flag.get("confirmed") is False
                        else "疑似漏检轴线；若确为不等跨请人工确认"))),
                **flag,
            })

    # 一图多视图的分幅：各区**只单向**标注轴号（立面/剖面是投影图）。
    # §8.0.5 的分区在平面上两个方向都标轴号 —— 单向就是分幅的指纹。
    # 分幅没有分区号可确认，不该要人工给。
    if is_split_view(zone_records):
        result["is_split_view"] = True
        result["split_view_numbering"] = renumber_split_views(zone_records)
        warnings.append(SPLIT_VIEW_WARNING)
        for record in zone_records:
            record["needs_confirmation"] = False
        for axis in labelled:
            axis["zone_label_confirmed"] = True

    result["zones"] = zone_records
    result["axes"] = labelled
    result["axis_count"] = len(labelled)

    # ── 坐标标注 → 世界锚点(读不到就只降级这一段)──
    #
    # **可疑图跳过这一段**:这是全链路最贵的部分(逐引线裁图 + OCR),
    # 而可疑图的锚点根本不会被消费——建模侧已按
    # `suspect_symbol_field = false` 过滤(migration 042)。
    # 实测代价:单张 111 秒,全项目重跑估算 35 小时;跳过后只剩轴线几何。
    # 轴线本身照常产出并留档,跳过的只是锚点。
    if result["suspect_symbol_field"]:
        result["warnings"] = warnings
        return result

    try:
        _attach_world_anchors(result, segments, marked, labelled,
                              page_h=page_h, read_text=read_text,
                              directions=directions, warnings=warnings)
    except Exception as exc:                      # noqa: BLE001
        logger.warning("[axis_recognition] 坐标标注环节降级: %s", exc)
        warnings.append(f"坐标标注读取失败(OCR/几何):{exc}")

    # **轴距过密的二次判定**：带数判据看不到轴距（它在 bands 阶段就跑了），
    # 而实测 183 张假轴网正是「带数没超、轴线却密到 0.29 米」。
    # 需要比例尺才能算米轴距，所以只能等轴线出来后再判一次。
    gap_m = _median_axis_gap_m(result.get("axes"), scale_m_pt)
    if gap_m and not result.get("suspect_symbol_field"):
        if is_suspect_symbol_field(0, gap_m):
            result["suspect_symbol_field"] = True
            warnings.append(
                f"轴距中位数仅 {gap_m:.2f} 米,小于定位轴线的合理下限 "
                f"{MIN_PLAUSIBLE_AXIS_GAP_M} 米(§8 定位轴线用于主要承重构件定位)"
                " —— 疑为桩位/设备符号被当成轴号圈。轴线照常产出并留档,"
                "但不进入 3D 场景与世界锚点,待人工确认")
    result["warnings"] = warnings
    return result


#: 判定「同一条轴线」时偏移量的容差（图纸点）。两端的轴号圈由同一条轴线
#: 引出，位置误差只来自圈心测量，实测在 1pt 以内。
BOTH_END_OFFSET_TOL_PT = 2.0


#: 判「整数倍」的相对容差。实测测量误差在 5% 以内，
#: 而漏一条轴线造成的偏差是 100%（间距翻倍）——分界远得很。
GAP_MULTIPLE_TOLERANCE = 0.15
#: 只报 2 倍及以上；且倍数上限防止把巨大跨度报成一串漏检。
MAX_SUSPECT_MULTIPLE = 6


#: 缺口中点与圈候选的匹配容差（pt）。轴号圈直径实测 20~28pt，
#: 6pt 足够容纳测量误差而不会串到相邻轴线。
GAP_CIRCLE_MATCH_TOL_PT = 6.0


def suspect_missing_axis_gaps(axes: list[dict],
                              circle_offsets: list[float] | None = None,
                              ) -> list[dict]:
    """报出「某档间距恰是其余档的整数倍」——**中间很可能漏检了轴线**。

    **实测**（metro 首层框架梁配筋图）：⑦ 与 ⑪ 的轴号圈未检出，而轴号是
    按顺序推导的，于是余下 12 个圈被标成 1~12，其后编号**整体偏移**。
    两处双倍间距（309 vs 155）就摆在数据里，只是没人看。

    **只报不猜**：GB 不禁止不等跨，一个真的 18600 跨同样呈现双倍间距。
    按倍数直接跳号会造出新的一类错误 —— 交给既有的人工确认通道，
    这与本模块「判不出就说判不出、降级必须可见」的一贯做法一致。

    **`circle_offsets` 把「可疑」变成「确认」**：给出全部圈候选的法向偏移后，
    检查缺口中点处有没有圈 —— 有就是真漏检（圈被某道闸挡掉了），
    没有就多半是不等跨。实测 4 张被标出的结构图，两者各占一半：
    单看间距的精度只有约 50%，加上圈候选后判断近乎确定。
    不给时 `confirmed` 为 `None`（判不出就不判）。
    """
    ordered = [a for a in (axes or []) if a.get("offset_pt") is not None]
    if len(ordered) < 4:
        return []                      # 少于 4 条定不出「其余档」的基准
    ordered = sorted(ordered, key=lambda a: -float(a["offset_pt"]))
    gaps = [abs(float(b["offset_pt"]) - float(a["offset_pt"]))
            for a, b in zip(ordered, ordered[1:])]
    positive = sorted(g for g in gaps if g > 0)
    if not positive:
        return []
    base = positive[len(positive) // 4]        # 下四分位：受漏检拉大影响最小

    out: list[dict] = []
    for index, gap in enumerate(gaps):
        if base <= 0:
            break
        ratio = gap / base
        multiple = round(ratio)
        if multiple < 2 or multiple > MAX_SUSPECT_MULTIPLE:
            continue
        if abs(ratio - multiple) > GAP_MULTIPLE_TOLERANCE:
            continue                   # 不成整数倍 → 是设计上的不等跨
        confirmed = None
        if circle_offsets is not None:
            a_off = float(ordered[index]["offset_pt"])
            b_off = float(ordered[index + 1]["offset_pt"])
            mids = [a_off + (b_off - a_off) * k / multiple
                    for k in range(1, multiple)]
            confirmed = any(
                any(abs(c - mid) <= GAP_CIRCLE_MATCH_TOL_PT
                    for c in circle_offsets)
                for mid in mids)
        out.append({
            "after_label": ordered[index].get("label"),
            # **补回发生在编号之前**，那时还没有 label，只能靠下标定位
            "after_index": index,
            "multiple": multiple,
            "gap_pt": round(gap, 2),
            "base_pt": round(base, 2),
            "confirmed": confirmed,
        })
    return out


def recover_gap_axes(axes: list[dict],
                     circle_offsets: list[float] | None) -> list[dict]:
    """把**确认漏检**的轴线补回来，在编号之前。

    补回的证据是：那个圈恰好落在已确认轴线构成的**模数网格**上。
    这比任何一道闸的判断都硬 —— 闸按局部几何判（圈边有没有线、
    圆里有没有横线），网格是全局规律。

    **实测**（metro 首层框架梁配筋图）：⑦、⑪ 的圈被邻近判据挡掉，
    余下 12 个圈被顺序标成 1~12，物理位置却是 ①②③④⑤⑥⑧⑨⑩⑫⑬⑭ ——
    漏一条不只少一条，**其后编号全错**。

    只补「确认」的（`confirmed` 为真）；缺口处无圈的一概不动，
    那多半是设计上的不等跨。补回的轴线带 `recovered` 标记，可追溯。
    """
    if circle_offsets is None or not axes:
        return axes
    flags = suspect_missing_axis_gaps(axes, circle_offsets=circle_offsets)
    confirmed = [f for f in flags if f.get("confirmed")]
    if not confirmed:
        return axes

    ordered = sorted(axes, key=lambda a: -float(a["offset_pt"]))
    extra: list[dict] = []
    for flag in confirmed:
        index = flag.get("after_index")
        if index is None or index + 1 >= len(ordered):
            continue
        a_off = float(ordered[index]["offset_pt"])
        b_off = float(ordered[index + 1]["offset_pt"])
        for k in range(1, flag["multiple"]):
            mid = a_off + (b_off - a_off) * k / flag["multiple"]
            hit = next((c for c in circle_offsets
                        if abs(c - mid) <= GAP_CIRCLE_MATCH_TOL_PT), None)
            if hit is None:
                continue
            extra.append({**{key: val for key, val in ordered[index].items()
                             if key not in ("label", "label_source")},
                          "offset_pt": hit, "recovered": True})
    if not extra:
        return axes
    return sorted([*axes, *extra], key=lambda a: -float(a["offset_pt"]))


def merge_both_end_labels(axes: list[dict]) -> list[dict]:
    """合并「同一条轴线两端各注一个轴号」造成的重复。

    **GB/T 50001 §8.0.2 允许轴号注写在轴线两端**，而识别器把两端的轴号带
    切成了两个分区，于是同一批轴线被数了两遍。实测（首层框架梁平面整体
    配筋图）：

        区0 轴号: 1@-542 2@-697 … 12@-2522   (图上边 y=227)
        区1 轴号: 1@-542 2@-697 … 12@-2522   (图下边 y=1275)
                       ↑ 偏移量完全相同

    该图识别 38 条、真实 22 条。整体重复率：大歌剧院 4%、轨道交通 **18%**，
    受影响图纸 17% / **40%**。

    **判据同时要三样**：标签相同、方向相同、偏移量几乎相同。
    §8.0.5 的真分区里 `1-1` 与 `2-1` 位置不同，不会被误合；
    互相垂直的两条轴线可能偏移量相同，靠方向分开。
    """
    kept: list[dict] = []
    for axis in axes or []:
        label = axis.get("label")
        kind = axis.get("label_kind")
        angle = round(float(axis.get("angle_deg") or 0.0), 1)
        offset = float(axis.get("offset_pt") or 0.0)
        twin = next(
            (k for k in kept
             if k.get("label") == label and k.get("label_kind") == kind
             and round(float(k.get("angle_deg") or 0.0), 1) == angle
             and abs(float(k.get("offset_pt") or 0.0) - offset)
             <= BOTH_END_OFFSET_TOL_PT),
            None)
        if twin is None:
            kept.append(dict(axis))
            continue
        # 两端的圈都属于这一条轴线
        twin["circle_count"] = (twin.get("circle_count") or 1) + (
            axis.get("circle_count") or 1)
    return kept


def _median_axis_gap_m(axes: list | None,
                       scale_m_pt: float | None) -> float | None:
    """轴线的中位间距(米);无比例尺或轴线太少 → None(**判不出就说判不出**)。"""
    if not axes or not scale_m_pt or scale_m_pt <= 0:
        return None
    offsets = sorted({round(float(a["offset_pt"]), 3) for a in axes
                      if a.get("offset_pt") is not None})
    gaps = [b - a for a, b in zip(offsets, offsets[1:]) if b > a]
    if len(gaps) < 3:
        return None
    from statistics import median

    return float(median(gaps)) * float(scale_m_pt)


def _empty(page_w: float, page_h: float, circle_count: int) -> dict:
    return {
        "page_w": page_w, "page_h": page_h,
        "circle_count": circle_count, "additional_count": 0,
        "axis_count": 0, "leader_count": 0,
        "zones": [], "axes": [], "anchors": [], "outliers": [],
        "violations": [], "warnings": [], "transform": None,
        # 疑为设备符号场(见 SYMBOL_FIELD_BAND_HINT)。轴线照常产出,
        # 但消费方应跳过 —— 标记而非拦截。
        "suspect_symbol_field": False,
        # 一图多视图的分幅（非 §8.0.5 分区）。分幅没有分区号可确认，
        # 不该挂在人工队列上；轴号应当跨幅连续编号。
        "is_split_view": False,
        "split_view_numbering": [],
    }


def _zone_violations(index: int, axes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for kind in ("numeric", "alpha"):
        labels = [a["label"] for a in axes if a["label_kind"] == kind]
        if not labels:
            continue
        for violation in validate_axis_labels(labels, kind=kind):
            out.append({**violation, "zone_index": index, "kind": kind})
    return out


def _attach_world_anchors(result: dict, segments: list[tuple],
                          all_circles: list[dict], labelled: list[dict], *,
                          page_h: float, read_text, directions, warnings) -> None:
    """引线 → OCR → 符号修复 → RANSAC → 轴号对锚点。就地写入 result。"""
    if not segments:
        return

    # 引线末端可能落在**附加轴线**上,所以用全部轴线找末端;
    # 而轴号对身份只认主轴线(附加轴线没有主序号)
    all_axes = bands_to_axes(detect_bands(all_circles, directions=directions),
                             page_h=page_h)
    leaders = find_leaders(segments, all_axes or labelled)
    result["leader_count"] = len(leaders)
    if not leaders:
        return

    pairs: list[dict] = []
    for leader in leaders:
        try:
            tokens = read_text(leader)
        except Exception as exc:                  # noqa: BLE001
            warnings.append(f"OCR 读取失败:{exc}")
            return
        value = parse_coordinate_tokens(tokens)
        if value:
            pairs.append({"page": leader["tip"],
                          "world": (value["x"], value["y"])})
    if not pairs:
        return

    # X 常聚成一簇,孤立的反号必是 OCR 丢了负号;Y 正负本就混杂,交给变换判
    xs = repair_sign_by_consensus([p["world"][0] for p in pairs])
    pairs = [{"page": p["page"], "world": (x, p["world"][1])}
             for p, x in zip(pairs, xs)]

    fit = ransac_similarity(pairs)
    checked = repair_outliers_by_transform(pairs, fit)
    result["outliers"] = [
        {"page": list(p["page"]), "world": list(p["world"])}
        for p in checked if p["outlier"]
    ]
    # **只用已确认分区的轴线做锚点身份**:锚点的身份是轴号对(label_x × label_y),
    # 分区号未确认时轴号退化成裸标签,两个分区的 `1 × A` 会撞成同一身份被去重
    # ——实测锚点因此从 8 掉到 7。宁可少写,也不能写一个会串图的错身份。
    identified = [a for a in labelled if a.get("zone_label_confirmed")]
    result["anchors"] = anchor_records(checked, identified, page_h=page_h)

    if fit:
        transform = transform_from_axes(
            labelled, page_h=page_h, scale_m_pt=fit["transform"]["scale"])
        result["transform"] = {
            "scale_m_pt": fit["transform"]["scale"],
            "rotation_deg": fit["transform"]["rotation_deg"],
            "rmse_m": fit["rmse"],
            "inliers": len(fit["inliers"]),
            "origin_x": transform.origin_x if transform else None,
            "origin_y": transform.origin_y if transform else None,
        }


def summarize(result: dict) -> dict:
    """摘要:直接回答「有多少事等人处理」。"""
    return {
        "zones": len(result["zones"]),
        # 单分区图不需要确认,不该被算成「等我处理」
        "zones_pending_label": sum(
            1 for z in result["zones"] if z.get("needs_confirmation")),
        "axes": result["axis_count"],
        "additional_axes": result["additional_count"],
        "anchors": len(result["anchors"]),
        "outliers": len(result["outliers"]),
        "violations": len(result["violations"]),
        "rmse_m": (result["transform"] or {}).get("rmse_m"),
    }


# ── 识别结果 → 3D 场景轴网 ────────────────────────────────────────

#: 判为正交的角度容差(度)
_ORTHO_TOLERANCE_DEG = 3.0


def axes_to_scene(axes: list[dict], transform, *,
                  suspect: bool = False) -> dict:
    """识别出的轴线 → scene 轴网格式 `{"x": [[label, pos_m]], "y": [...]}`。

    与 `model_elements.archive_axes_to_scene` 同契约:x 是竖轴(数字轴号)、
    y 是横轴(字母轴号),位置为**本图米坐标**。

    **只输出正交轴线**:scene 的轴网格式是「一维位置 + 标签」,
    表达不了旋转分区的斜轴(实测 A-01-02A 的分区 3 是 42°/132°)。
    斜轴如实跳过,而不是硬投影成一个错位置——投影后的位置在模型里会指向别处。

    位置换算依据法向偏移的定义:90° 轴线的偏移是 `-x`,0° 轴线的偏移是 `y`。
    """
    from services.drawing_transform import pt_to_meter

    out: dict[str, list] = {"x": [], "y": []}
    # 疑为设备符号场时不进模型 —— 这是 `suspect_symbol_field` 标记的落点:
    # 识别结果照常留档可查,但不污染 3D 场景(见 SYMBOL_FIELD_BAND_HINT)。
    if not axes or transform is None or suspect:
        return out
    for axis in axes:
        # **只输出已确认分区的轴号** —— 与世界锚点同一条规则:标签就是身份。
        # 分区号未确认时三个分区各自从 1 开始,scene 里会出现重复的 `1`,
        # 而 `_merge_axes` 会拿带标签的去升级无标签的,歧义标签会污染别的轴线。
        if not axis.get("zone_label_confirmed"):
            continue
        label = str(axis.get("label") or "").strip()
        if not label:
            continue
        angle = float(axis.get("angle_deg", 0.0)) % 180.0
        offset = float(axis.get("offset_pt", 0.0))
        if abs(angle - 90.0) <= _ORTHO_TOLERANCE_DEG:
            x_m, _y = pt_to_meter(-offset, 0.0, transform)
            out["x"].append([label, x_m])
        elif angle <= _ORTHO_TOLERANCE_DEG or angle >= 180.0 - _ORTHO_TOLERANCE_DEG:
            _x, y_m = pt_to_meter(0.0, offset, transform)
            out["y"].append([label, y_m])
        # 斜轴:scene 格式表达不了,跳过(不硬投影成错位置)
    return out
