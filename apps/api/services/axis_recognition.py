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


def is_suspect_symbol_field(band_count: int) -> bool:
    """带数是否多到疑为设备符号场。

    抽成纯函数,是为了让判据本身可直接测——用合成圆圈去凑出 60+ 条带
    会跟贪心分带算法较劲(错开的行会形成近竖向链被整条吸走),
    测出来的是分带算法而不是这条判据。
    """
    return band_count > SYMBOL_FIELD_BAND_HINT

#: 一个分区至少要有一对**互相垂直**的带(横行标注竖向轴线、竖列标注横向轴线)。
#: **此前这里用「成员数 ≥ 8」当主带门槛,那是从验证脚本带过来的魔数**——
#: 小图纸的分区可能只有五六条轴线,会被整个漏掉。改用「配上对没有」作判据:
#: 配上对的成带成区,落单的再按分区二维范围吸附。
MIN_BANDS_PER_ZONE = 2


def recognize(circles: list[dict], *, strokes: list[tuple],
              segments: list[tuple], page_w: float, page_h: float,
              read_text, zone_labels: dict[int, str] | None = None,
              directions: tuple[float, ...] = DEFAULT_DIRECTIONS) -> dict:
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
        axes = derive_zone_labels(bands_to_axes(zone["bands"], page_h=page_h),
                                  zone=label)
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

    result["warnings"] = warnings
    return result


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
