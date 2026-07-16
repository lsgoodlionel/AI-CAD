"""围护桩/钢立柱圆检测(Phase E3 / 路径B)。

歌剧院等纯 PDF 图纸把桩/圆柱画成圆(短线段/弧近似),几何识别器 key 的是
闭合近方多段线,抓不到圆。栅格化 + OpenCV HoughCircles 能稳定检出圆形桩
(实测围护体平面 196、剖面仅 19,特异性好)。

分层:
- octagon_outline / circle_px_to_meter：纯几何/变换,离线可测
- detect_circles_px：cv2.HoughCircles 薄封装(合成图可测)
- detect_pile_columns：栅格化 PDF → 检圆 → 米坐标八边形柱(IO,优雅降级)

坐标系与 element_recognizer._Ctx.to_m 一致(y 翻转 + 轴网原点平移 + 比例),
保证圆柱与其余构件同坐标系。
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

DEFAULT_DPI = 150
# 桩/圆柱直径范围(米):下限 0.5 排除钢筋/引线小圆误检;上限 1.4 覆盖大直径桩
DEFAULT_SIZE_RANGE_M = (0.5, 1.4)
DEFAULT_PARAM2 = 32          # HoughGradient 累加阈值:越小越敏感(误检多)
_MAX_CIRCLES = 1500          # 单图圆柱上限(防噪声图刷爆)

# 八边形单位方向(近似圆,渲染/算量足够)
_OCT_DIRS = [
    (math.cos(math.pi * k / 4), math.sin(math.pi * k / 4)) for k in range(8)
]


def octagon_outline(cx: float, cy: float, r: float) -> list[list[float]]:
    """圆(cx,cy,r)→ 八边形顶点(米坐标),作为柱 outline。"""
    return [[round(cx + r * ux, 3), round(cy + r * uy, 3)] for ux, uy in _OCT_DIRS]


def circle_px_to_meter(
    cx_px: float, cy_px: float, r_px: float, *,
    dpi: int, page_h_pt: float, scale_m_pt: float, origin_pt: tuple[float, float],
) -> tuple[float, float, float]:
    """像素圆心/半径 → 米坐标(与 _Ctx.to_m 同口径:px→pt→翻转平移比例)。"""
    pt_per_px = 72.0 / dpi
    cx_pt = cx_px * pt_per_px
    cy_pt = cy_px * pt_per_px
    r_pt = r_px * pt_per_px
    fx = cx_pt - origin_pt[0]
    fy = (page_h_pt - cy_pt) - origin_pt[1]
    return fx * scale_m_pt, fy * scale_m_pt, r_pt * scale_m_pt


def detect_circles_px(
    gray_image, min_r_px: int, max_r_px: int, param2: int = DEFAULT_PARAM2,
) -> list[tuple[float, float, float]]:
    """cv2.HoughCircles 薄封装;返回 [(x_px, y_px, r_px)]。cv2 缺失/无圆 → []。"""
    try:
        import cv2
    except ImportError:
        return []
    if min_r_px < 1:
        min_r_px = 1
    if max_r_px <= min_r_px:
        max_r_px = min_r_px + 2
    circles = cv2.HoughCircles(
        gray_image, cv2.HOUGH_GRADIENT, dp=1, minDist=max(min_r_px, 8),
        param1=100, param2=param2, minRadius=min_r_px, maxRadius=max_r_px,
    )
    if circles is None:
        return []
    return [(float(c[0]), float(c[1]), float(c[2])) for c in circles[0]]


def detect_pile_columns(
    pdf_bytes: bytes, geom, *, dpi: int = DEFAULT_DPI,
    size_range_m: tuple[float, float] = DEFAULT_SIZE_RANGE_M,
    param2: int = DEFAULT_PARAM2, src: str = "",
) -> list[dict]:
    """栅格化 PDF → HoughCircles → 米坐标八边形柱(shape=circle)。

    任何依赖缺失/异常一律返回 []（优雅降级,绝不阻断建模)。
    坐标变换复用 element_recognizer 的轴网/比例/原点检测,保证同坐标系。
    """
    try:
        import cv2
        import fitz
        import numpy as np

        from .element_recognizer import _detect_axes, _detect_scale, _origin_pt

        axis_x, axis_y, _ = _detect_axes(
            geom.lines, geom.page_w, geom.page_h, geom.texts
        )
        all_text = " ".join(t[2] for t in geom.texts)
        scale = _detect_scale(all_text, geom.page_w, axis_x, axis_y)
        if scale <= 0:
            return []
        origin = _origin_pt(axis_x, axis_y, geom.page_h)

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, 3
            )
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        finally:
            doc.close()

        m_per_px = scale * 72.0 / dpi
        if m_per_px <= 0:
            return []
        min_r = max(int(size_range_m[0] / 2 / m_per_px), 4)
        max_r = max(int(size_range_m[1] / 2 / m_per_px), min_r + 2)

        circles = detect_circles_px(gray, min_r, max_r, param2)
        columns: list[dict] = []
        for cx_px, cy_px, r_px in circles[:_MAX_CIRCLES]:
            cx_m, cy_m, r_m = circle_px_to_meter(
                cx_px, cy_px, r_px,
                dpi=dpi, page_h_pt=geom.page_h, scale_m_pt=scale, origin_pt=origin,
            )
            columns.append({
                "outline": octagon_outline(cx_m, cy_m, r_m),
                "src": src,
                "shape": "circle",
            })
        return columns
    except Exception as exc:  # noqa: BLE001 — 圆检测失败降级,不阻断建模
        logger.warning("[circle_detector] 圆检测跳过: %s", exc)
        return []


def dedupe_against(columns: list[dict], existing: list[dict], tol_m: float = 0.6) -> list[dict]:
    """去重:剔除与已识别柱质心相近(容差内)的圆柱,避免重复计数。"""
    def _centroid(el: dict) -> tuple[float, float]:
        pts = el.get("outline") or []
        if not pts:
            return (0.0, 0.0)
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    seen = [_centroid(e) for e in existing]
    out: list[dict] = []
    for col in columns:
        cx, cy = _centroid(col)
        if any(abs(cx - sx) < tol_m and abs(cy - sy) < tol_m for sx, sy in seen):
            continue
        seen.append((cx, cy))
        out.append(col)
    return out
