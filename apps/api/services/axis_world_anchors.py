"""识别成果 → 世界锚点记录(Phase I 的 M-I5 接线)。

坐标标注(`core.model3d.coord_annotation`)给出的是「页面点 ↔ 工程坐标」,
而 `axis_intersections` 的身份是**轴号对**(label_x × label_y,见 migration 039)
——因为轴号对才是跨图对齐的天然锚点。所以要把每个锚点落回它所在的两条轴线上:

    label_x  竖向轴号(数字向,如 1-1)
    label_y  横向轴号(字母向,如 1-A)

**归一化口径**:x_norm / y_norm 都是**同除页高**,与
`model_world_placement.intersections_to_meter` 保持一致——各除各的会让 x 方向
整体缩放错。

**为什么必须排除外点**:RANSAC 标出的粗错(实测 1 处:OCR 把 -156.750 读成
-1.000)一旦写进锚点表,整张图会被摆到错误的位置。**错的世界坐标比缺一个锚点
危险得多**,宁可少写几个。
"""
from __future__ import annotations

from core.model3d.axis_normal import normal_offset

#: 点到轴线的最大法向距离(pt)。实测最小轴距约 26pt,容差必须远小于它,
#: 否则会把锚点配到相邻轴线上——那等于给了它一个错的身份
AXIS_MATCH_TOLERANCE_PT = 3.0

#: 自动锚点的来源标记。人审时要能一眼分清是算出来的还是人标的
AUTO_NOTE = "auto:coord_annotation"


def nearest_labelled_axis(point: tuple[float, float], axes: list[dict],
                          kind: str,
                          tol: float = AXIS_MATCH_TOLERANCE_PT) -> dict | None:
    """点所在的那条轴线(限定数字向或字母向);够不着返回 None。"""
    candidates = [a for a in axes if a.get("label_kind") == kind]
    if not candidates:
        return None
    best = min(candidates,
               key=lambda a: abs(normal_offset(point[0], point[1],
                                               a["angle_deg"]) - a["offset_pt"]))
    gap = abs(normal_offset(point[0], point[1], best["angle_deg"])
              - best["offset_pt"])
    return best if gap <= tol else None


def anchor_records(anchors: list[dict], axes: list[dict], *, page_h: float,
                   tol: float = AXIS_MATCH_TOLERANCE_PT) -> list[dict]:
    """锚点 + 带轴号的轴线 → `axis_intersections` 记录(不改入参)。

    anchors 每项形如 `{"page": (x, y), "world": (wx, wy)}`,可带
    `outlier` / `repaired` 标记(由 `coord_annotation.repair_outliers_by_transform` 产出)。

    只有**同时落在一条数字轴线和一条字母轴线上**的锚点才入表——
    没有轴号对就没有跨图对齐的身份。
    """
    if page_h <= 0:
        return []
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for anchor in anchors:
        if anchor.get("outlier"):
            continue                       # 粗错绝不入表
        px, py = anchor["page"]
        axis_x = nearest_labelled_axis((px, py), axes, "numeric", tol)
        axis_y = nearest_labelled_axis((px, py), axes, "alpha", tol)
        if not axis_x or not axis_y:
            continue                       # 缺一半身份,不入表
        key = (axis_x["label"], axis_y["label"])
        if key in seen:
            continue
        seen.add(key)
        note = AUTO_NOTE
        if anchor.get("repaired"):
            note = f"{AUTO_NOTE}:{anchor['repaired']}"
        wx, wy = anchor["world"]
        out.append({
            "label_x": axis_x["label"],
            "label_y": axis_y["label"],
            "x_norm": round(px / page_h, 6),
            "y_norm": round(py / page_h, 6),
            "world_x": wx,
            "world_y": wy,
            "note": note,
        })
    return out


async def persist_anchors(db, project_id: str, drawing_id: str,
                          records: list[dict]) -> int:
    """把锚点记录写入 `axis_intersections`,返回写入条数。

    走既有 repo 的 upsert(唯一键是 drawing_id × label_x × label_y),
    重复运行不会堆积。
    """
    from services.axis_intersection_repo import save_intersection

    written = 0
    for record in records:
        # created_by=None 表示自动锚点(人工标定会带 user id),来源另见 note
        await save_intersection(db, project_id=project_id, drawing_id=drawing_id,
                                point=record, created_by=None)
        written += 1
    return written


# ── 由识别结果构造 drawing_transform ────────────────────────────────

def transform_from_axes(axes: list[dict], *, page_h: float,
                        scale_m_pt: float):
    """带轴号的轴线 + 实测比例 → `DrawingTransform`。

    **为什么不用老的 `transform_from_geometry`**:它靠 `_detect_scale` 从图面文字
    里读比例尺,而这类定位图的文字全是描边字形,读不到——实测 A-01-02A 根本
    没有 drawing_transform 记录,于是 `placements_for_project` 直接跳过它。
    这里的 `scale_m_pt` 来自坐标标注 RANSAC 拟合(实测 0.142757 m/pt,残差 5.7mm),
    比从文字里猜可靠得多。

    **口径**:与 `pt_to_meter` 一致——x 直接平移,y 先翻转再平移。
    原点取轴网的左下角(数字向轴线的最小 x、字母向轴线的最小翻转 y),
    与 `element_recognizer._origin_pt` 同约定。

    轴线不足以定原点时返回 None(不落无效变换,让下游诚实降级)。
    """
    from services.drawing_transform import DrawingTransform

    if page_h <= 0 or scale_m_pt <= 0:
        return None
    xs = [-a["offset_pt"] for a in axes if a.get("label_kind") == "numeric"]
    ys = [page_h - a["offset_pt"] for a in axes if a.get("label_kind") == "alpha"]
    if not xs or not ys:
        return None
    return DrawingTransform(
        scale_m_pt=float(scale_m_pt),
        origin_x=float(min(xs)),
        origin_y=float(min(ys)),
        page_h=float(page_h),
        confidence=1.0,
    )
