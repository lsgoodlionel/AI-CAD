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


@dataclass(frozen=True)
class DrawingTransform:
    scale_m_pt: float
    origin_x: float
    origin_y: float
    page_h: float
    confidence: float | None = None


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
        if not scale or scale <= 0:
            return None
        origin = _origin_pt(axis_x, axis_y, geom.page_h)
        labeled = sum(1 for label, _ in (*axis_x, *axis_y) if str(label or "").strip())
        confidence = round(labeled / total, 4) if total else 0.0
        return DrawingTransform(
            scale_m_pt=float(scale),
            origin_x=float(origin[0]),
            origin_y=float(origin[1]),
            page_h=float(geom.page_h),
            confidence=confidence,
        )
    except Exception:  # noqa: BLE001 — 变换算不出则不落,下游降级
        return None


_UPSERT_SQL = """
INSERT INTO drawing_transform
    (drawing_id, project_id, scale_m_pt, origin_x, origin_y, page_h, confidence, updated_at)
VALUES
    (:drawing_id, :project_id, :scale_m_pt, :origin_x, :origin_y, :page_h, :confidence, now())
ON CONFLICT (drawing_id) DO UPDATE SET
    scale_m_pt = EXCLUDED.scale_m_pt,
    origin_x = EXCLUDED.origin_x,
    origin_y = EXCLUDED.origin_y,
    page_h = EXCLUDED.page_h,
    confidence = EXCLUDED.confidence,
    updated_at = now()
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
    })


_FETCH_SQL = """
SELECT drawing_id, scale_m_pt, origin_x, origin_y, page_h, confidence
FROM drawing_transform WHERE project_id = :project_id
"""


async def fetch_project_transforms(db: Any, project_id: str) -> dict[str, DrawingTransform]:
    """取全项目各图变换,返回 {drawing_id: DrawingTransform}。"""
    rows = await db.fetch_all(_FETCH_SQL, {"project_id": project_id})
    out: dict[str, DrawingTransform] = {}
    for r in rows:
        out[str(r["drawing_id"])] = DrawingTransform(
            scale_m_pt=float(r["scale_m_pt"]),
            origin_x=float(r["origin_x"]),
            origin_y=float(r["origin_y"]),
            page_h=float(r["page_h"]),
            confidence=float(r["confidence"]) if r["confidence"] is not None else None,
        )
    return out
