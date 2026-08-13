"""每图坐标变换:pt→米(Phase E 路径C-A1)。

档案里页面点(pt)坐标的信息(轴号/文字位置)要进 3D 模型需转米坐标。
变换三要素与 element_recognizer._Ctx.to_m 同口径:
  x_m = (x_pt - origin_x) * scale
  y_m = ((page_h - y_pt) - origin_y) * scale

抽取时由 transform_from_geometry 复用识别器的 _detect_axes/_detect_scale/
_origin_pt 算出并 persist_transform 落库(drawing_transform 表,migration 031)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 1 排版点 = 25.4/72 mm。比例分母与 `scale_m_pt` 的换算基准。
PT_TO_MM = 25.4 / 72

#: GB/T 50001 **§6.0.4 表 6.0.4「绘图所用的比例」** 的常用比例分母。
#: 门禁区间必须包住它们——把合法比例挡在外面比放进错的更糟。
STANDARD_SCALE_DENOMINATORS: tuple[int, ...] = (
    1, 2, 3, 4, 5, 6, 10, 15, 20, 25, 30, 40, 50, 60, 80,
    100, 150, 200, 250, 300, 400, 500, 600, 1000, 1500, 2000,
)

#: 比例尺合理区间（米/pt）。下限对应 1:1（建筑图最大就是原尺寸），
#: 上限放到 1:5000（总平面图量级，比 §6.0.4 表的 1:2000 留一档余量）。
#:
#: **为什么必须有上限**：`transform_from_geometry` 原先只查 `scale <= 0`，
#: 于是实测 **35 张**图的比例分母超出国标区间，最离谱的 `A-10-07.1C`
#: 是 **1:3358662**——构件会被扔到几百公里外。而它们的 `confidence`
#: **全是 1.00**，因为旧公式算的是「带标签轴线数/轴线总数」，
#: 衡量的是轴号识别质量，与比例尺对错无关。
#:
#: 后果实测：同层两张图的构件中心差 **83~103 米**（F3/F2），
#: 这正是「模型轴线和结构位置不对」的直接原因。
MIN_SCALE_M_PT = 1 * PT_TO_MM / 1000.0
MAX_SCALE_M_PT = 5000 * PT_TO_MM / 1000.0

#: 比例分母与国标常用值的相对偏差在此以内，视为标准比例（confidence 更高）。
STANDARD_SCALE_TOLERANCE = 0.02

#: 吸附容差。**§6.0.4 表 6.0.4 的比例是离散规定值**——图纸的真实比例只能是
#: 表里的某一个，实测出 45.6 只能意味着真值是 50，差的那 8.8% 是测量误差。
#:
#: 实测全项目 1429 条变换:1264 条已在 ±2% 内、**113 条落在 2%~10%
#: （平均偏差 4.9%）**、37 条在 10%~30%、15 条超过 30%。
#: 4.9% 的比例误差在 100 米建筑上就是 **4.9 米位置误差**。
#:
#: 取 10%:相邻标准值最密处是 1:5→1:6 与 1:50→1:60（都差 20%），
#: 10% 不会跨越到相邻值。偏差更大的**不吸附**——硬凑只会把错误固化。
SNAP_TOLERANCE = 0.10


def snap_scale_to_standard(scale_m_pt: float,
                           tolerance: float = SNAP_TOLERANCE) -> float:
    """把接近国标常用比例的实测值吸附到该标准值（§6.0.4）。

    偏差超过 `tolerance` 时**原样返回**——硬凑一个标准值只会把错误固化，
    而留着非标准值至少能让 confidence 反映出「这张图的比例可疑」。
    """
    denominator = scale_denominator(scale_m_pt)
    if denominator <= 0:
        return scale_m_pt
    best = min(STANDARD_SCALE_DENOMINATORS, key=lambda d: abs(d - denominator))
    if abs(best - denominator) <= best * tolerance:
        return best * PT_TO_MM / 1000.0
    return scale_m_pt


def scale_denominator(scale_m_pt: float) -> float:
    """`scale_m_pt` → 图纸比例分母（1:N 里的 N）。"""
    return scale_m_pt * 1000.0 / PT_TO_MM


def is_scale_plausible(scale_m_pt: float) -> bool:
    """比例尺是否落在国标可能的区间内（见 MIN/MAX_SCALE_M_PT）。

    **宁可没有变换，也不能有错变换**：没有变换时下游会降级估位置，
    而错变换会把构件放到几百公里外，还带着满分置信度骗过所有下游。
    """
    return MIN_SCALE_M_PT <= float(scale_m_pt or 0.0) <= MAX_SCALE_M_PT


def is_standard_scale(scale_m_pt: float,
                      tolerance: float = STANDARD_SCALE_TOLERANCE) -> bool:
    """是否是 §6.0.4 表里的常用比例。"""
    denominator = scale_denominator(scale_m_pt)
    return any(abs(denominator - d) <= d * tolerance
               for d in STANDARD_SCALE_DENOMINATORS)


#: 变换的写入来源。一图一行而三条路径都往这行写 ——
#: 没有来源就无从判断「该不该清掉这条陈旧变换」（migration 047）。
TRANSFORM_SOURCE_AXES = "axes"          # Phase I 轴网路径
TRANSFORM_SOURCE_GEOMETRY = "geometry"  # 图面文字读比例
TRANSFORM_SOURCE_MANUAL = "manual"      # 人工确认端点
#: 迁移前的历史行 —— 来源**不可考**，一律记 unknown，不猜。
#: 清理只动来源相符的行，于是 1436 条历史变换不会被误删。
TRANSFORM_SOURCE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class DrawingTransform:
    scale_m_pt: float
    origin_x: float
    origin_y: float
    page_h: float
    confidence: float | None = None
    #: 该方向**没有检出轴线**，原点按 0 兜底 —— 不是「原点真在 0」。
    #: 实测 1436 条里 x 缺 72 张、y 缺 77 张（10.4%），
    #: 而「两方向都缺」是 0 张 —— 它们缺的都是**一个**方向。
    #: 该方向的构件坐标从图幅边缘算起，会整体偏移 `真原点 × 比例`。
    origin_x_estimated: bool = False
    origin_y_estimated: bool = False
    #: 这条变换**是谁写的**（见 TRANSFORM_SOURCE_*）。
    source: str = TRANSFORM_SOURCE_UNKNOWN


def pt_to_meter(x_pt: float, y_pt: float, t: DrawingTransform) -> tuple[float, float]:
    """页面点 → 米(与 _Ctx.to_m 同口径:y 翻转 + 轴网原点平移 + 比例)。"""
    fx = x_pt - t.origin_x
    fy = (t.page_h - y_pt) - t.origin_y
    return round(fx * t.scale_m_pt, 3), round(fy * t.scale_m_pt, 3)


def transform_from_geometry(geom: Any) -> DrawingTransform | None:
    """从几何算坐标变换;比例尺检测失败(<=0)返回 None(不落无效变换)。"""
    try:
        from core.model3d.element_recognizer import (
            _detect_axes,
            _detect_scale,
            _origin_pt,
        )

        axis_x, axis_y, _ = _detect_axes(
            geom.lines, geom.page_w, geom.page_h, geom.texts
        )
        total = len(axis_x) + len(axis_y)
        # 无轴线 → 原点不可靠(pt→米会错位),不落变换,下游降级
        if total == 0:
            return None
        all_text = " ".join(t[2] for t in geom.texts)
        scale = _detect_scale(all_text, geom.page_w, axis_x, axis_y)
        # **比例尺合理性门禁**（§6.0.4）——超出国标区间就不落变换。
        # 旧实现只查 `scale <= 0`，放进了 35 张离谱变换（最大 1:335 万）。
        if not is_scale_plausible(scale):
            return None
        # 吸附到 §6.0.4 的离散比例——实测 113 张图的比例有 4.9% 的测量误差，
        # 在 100 米建筑上就是 4.9 米位置误差。
        scale = snap_scale_to_standard(scale)
        origin = _origin_pt(axis_x, axis_y, geom.page_h)
        # confidence 要同时反映**轴号识别质量**与**比例尺是否标准**。
        # 旧公式只有前者，于是比例错到 1:335 万仍是满分。
        labeled = sum(1 for label, _ in (*axis_x, *axis_y) if str(label or "").strip())
        label_score = (labeled / total) if total else 0.0
        scale_score = 1.0 if is_standard_scale(scale) else 0.5
        confidence = round(label_score * scale_score, 4)
        # **原点缺失时按 0 落库但标记出来** —— 不拒绝,因为拒绝会让
        # 149 张(10.4%)图失去定位、影响面大;标记是纯增量,
        # 下游(包络/校验)可据此排除该方向（「降级必须可见」）。
        origin_x_missing = origin[0] is None
        origin_y_missing = origin[1] is None
        return DrawingTransform(
            scale_m_pt=float(scale),
            origin_x=float(origin[0] or 0.0),
            origin_y=float(origin[1] or 0.0),
            origin_x_estimated=origin_x_missing,
            origin_y_estimated=origin_y_missing,
            page_h=float(geom.page_h),
            confidence=confidence,
            source=TRANSFORM_SOURCE_GEOMETRY,
        )
    except Exception:  # noqa: BLE001 — 变换算不出则不落,下游降级
        return None


#: **人工确认的比例尺不被自动路径覆盖**。
#:
#: 与 `filter_scene_axes` 对 `axes_source == "manual"` 的处置同源：
#: 自动机制是用来挡自动识别的错值的，不该推翻人核过的真值。
#: 没有这个 WHERE，一次重跑识别就会把人工确认结果冲掉。
_UPSERT_SQL = f"""
INSERT INTO drawing_transform
    (drawing_id, project_id, scale_m_pt, origin_x, origin_y, page_h, confidence,
     origin_x_estimated, origin_y_estimated, source, updated_at)
VALUES
    (:drawing_id, :project_id, :scale_m_pt, :origin_x, :origin_y, :page_h, :confidence,
     :origin_x_estimated, :origin_y_estimated, :source, now())
ON CONFLICT (drawing_id) DO UPDATE SET
    scale_m_pt = EXCLUDED.scale_m_pt,
    origin_x = EXCLUDED.origin_x,
    origin_y = EXCLUDED.origin_y,
    origin_x_estimated = EXCLUDED.origin_x_estimated,
    origin_y_estimated = EXCLUDED.origin_y_estimated,
    page_h = EXCLUDED.page_h,
    confidence = EXCLUDED.confidence,
    source = EXCLUDED.source,
    updated_at = now()
WHERE drawing_transform.source <> '{TRANSFORM_SOURCE_MANUAL}'
   OR EXCLUDED.source = '{TRANSFORM_SOURCE_MANUAL}'
"""

#: 只删**同一来源**的行。
#:
#: 轴网路径算不出变换时,库里那条若也是它上次写的,就已不代表当前识别结果,
#: 该清掉让下游诚实降级（「宁可没有变换，也不能有错变换」）;
#: 但若那行来自几何路径或人工确认,它与本次识别无关,**不得连坐**。
#: `unknown` 的历史行同理不动 —— 不知道是谁写的,就不能替它做主。
_DELETE_SQL = """
DELETE FROM drawing_transform
WHERE drawing_id = :drawing_id AND source = :source
"""


async def persist_transform(
    db: Any, *, project_id: str, drawing_id: str, transform: DrawingTransform,
) -> None:
    """落库单图坐标变换(幂等 upsert)。"""
    await db.execute(_UPSERT_SQL, {
        "drawing_id": drawing_id,
        "project_id": project_id,
        "scale_m_pt": transform.scale_m_pt,
        "origin_x": transform.origin_x,
        "origin_y": transform.origin_y,
        "page_h": transform.page_h,
        "confidence": transform.confidence,
        "origin_x_estimated": bool(transform.origin_x_estimated),
        "origin_y_estimated": bool(transform.origin_y_estimated),
        "source": transform.source or TRANSFORM_SOURCE_UNKNOWN,
    })


async def clear_transform(db: Any, *, drawing_id: str, source: str) -> None:
    """清掉**本来源**为该图写下的变换（算不出新值时调用）。

    实测 `S-0-20-102.04C` 的轴网识别跑于 06:02、变换停在 01:47 ——
    算不出时不清理，下游就一直在用上一次的过时值。
    """
    if source == TRANSFORM_SOURCE_MANUAL:
        # 人工确认值只由人自己改，自动路径无权清除。
        return
    await db.execute(_DELETE_SQL, {"drawing_id": drawing_id, "source": source})


_FETCH_SQL = """
SELECT drawing_id, scale_m_pt, origin_x, origin_y, page_h, confidence,
       origin_x_estimated, origin_y_estimated, source
FROM drawing_transform WHERE project_id = :project_id
"""


async def fetch_project_transforms(db: Any, project_id: str) -> dict[str, DrawingTransform]:
    """取全项目各图变换,返回 {drawing_id: DrawingTransform}。"""
    rows = await db.fetch_all(_FETCH_SQL, {"project_id": project_id})
    out: dict[str, DrawingTransform] = {}
    for r in rows:
        conf = _column(r, "confidence")
        out[str(r["drawing_id"])] = DrawingTransform(
            scale_m_pt=float(r["scale_m_pt"]),
            origin_x=float(r["origin_x"]),
            origin_y=float(r["origin_y"]),
            page_h=float(r["page_h"]),
            confidence=float(conf) if conf is not None else None,
            # **落了库要读得回来** —— 上一轮加了列与 SELECT 却漏了这两行赋值，
            # 于是下游（包络/校验）从来看不到「该方向原点是兜底的」。
            origin_x_estimated=bool(_column(r, "origin_x_estimated", False)),
            origin_y_estimated=bool(_column(r, "origin_y_estimated", False)),
            source=str(_column(r, "source", TRANSFORM_SOURCE_UNKNOWN)),
        )
    return out


def _column(row: Any, name: str, default: Any = None) -> Any:
    """取一列，缺列或为 NULL 时给默认值（迁移未跑时不炸）。"""
    try:
        value = row[name]
    except Exception:  # noqa: BLE001 — 行对象类型随驱动而异，缺列一律降级
        return default
    return default if value is None else value
