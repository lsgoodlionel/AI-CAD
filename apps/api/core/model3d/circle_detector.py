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
import threading

logger = logging.getLogger(__name__)

#: 栅格化 + OpenCV 段的互斥锁。
#:
#: **为什么需要**：实测 SIGSEGV —— 主流程 20 秒超时放弃某图后继续处理下一张，
#: 而被放弃的线程**仍在跑**（`wait_for` 取消不了 executor 里的同步函数），
#: 于是多个线程同时做 fitz 渲染 + cv2，原生库并发导致段错误：
#:
#: ```
#: 00:55:46 构件识别跳过 3ac7a958: TimeoutError   ← 主流程放弃
#: 00:55:53 [circle] 3ac7a958 超像素预算(53 MP)   ← 僵尸线程此刻才进圆检测
#: 00:55:58 signal 11 (SIGSEGV)
#: ```
#:
#: **这是圆检测提速后才暴露的**：此前线程池被慢检测占死，实际是串行的。
#: 临界区很短（2 MP 预算下单图 ~5 秒），串行化不损失吞吐 —— CPU 本已饱和。
_RENDER_LOCK = threading.Lock()

DEFAULT_DPI = 150
# 桩/圆柱直径范围(米):下限 0.5 排除钢筋/引线小圆误检;上限 1.4 覆盖大直径桩
DEFAULT_SIZE_RANGE_M = (0.5, 1.4)
DEFAULT_PARAM2 = 32          # HoughGradient 累加阈值:越小越敏感(误检多)
_MAX_CIRCLES = 1500          # 单图圆柱上限(防噪声图刷爆)
_MAX_RENDER_PX = 8000        # 仅超巨图降 dpi 兜底(150dpi 全分辨率保桩检出;+2705 实测在此成功)

#: HoughCircles 的**总像素预算**(百万像素)。
#:
#: **为什么最长边不够**：`_MAX_RENDER_PX` 限的是最长边，8000×6000 仍有
#: 4800 万像素。实测 v51 重建卡在「4层」**13 分钟零进展**，py-spy 抓到
#: 两个线程都停在 `detect_circles_px`，而且都是已被 20 秒超时「放弃」的
#: 僵尸——`asyncio.wait_for` 取消不了 executor 里的同步函数。
#: 线程池 `max_workers=2` 被占满后，后续每张图都在等一个永不空出的池：
#: **这不是慢，是死锁**。
#:
#: **24 MP 实测仍不够**：`S-1-32-103C 三~四层柱平面图`(53 MP 降到 24 MP 后)
#: 单张跑了 **5 分钟仍未返回**。HoughCircles 的开销主要在
#: 「边缘点数 × 半径档数」，而建筑图线条密集，24 MP 上的边缘点是千万量级。
#:
#: 在那张图上逐档实测（`S-1-32-103C`，原始 53 MP）：
#:
#: | 预算 | 耗时 | 检出 |
#: |---|---:|---:|
#: | 8 MP | 76.1 s | 137 |
#: | 4 MP | 21.9 s | 134 |
#: | **2 MP** | **5.7 s** | **129** |
#: | 1 MP | 1.7 s | 121 |
#:
#: **耗时超线性增长而检出几乎不变**（8→2 MP 快 13 倍，只少 6% 的圆）——
#: HoughCircles 的开销是「边缘点数 × 半径档数」，降采样同时压掉了两者，
#: 而桩的圆心位置在低分辨率下依然成立。
#:
#: 取 **2 MP** 还有个决定性理由：76 秒的结果**会被 20 秒超时丢弃**，
#: 实际检出是 **0**。所以这不是拿召回换速度，是把 0 换成 129。
#:
#: 超预算的图降采样后仍检测，降级记进日志（降级必须可见）。
MAX_RENDER_MEGAPIXELS = 2.0


def render_scale_for(*, width_px: float, height_px: float) -> float:
    """按总像素预算算渲染缩放比;在预算内返回 1.0（不误伤正常图）。"""
    pixels = max(float(width_px), 0.0) * max(float(height_px), 0.0)
    budget = MAX_RENDER_MEGAPIXELS * 1e6
    if pixels <= budget or pixels <= 0:
        return 1.0
    return (budget / pixels) ** 0.5

# 八边形单位方向(近似圆,渲染/算量足够)
_OCT_DIRS = [
    (math.cos(math.pi * k / 4), math.sin(math.pi * k / 4)) for k in range(8)
]


def octagon_outline(cx: float, cy: float, r: float) -> list[list[float]]:
    """圆(cx,cy,r)→ 八边形顶点(米坐标),作为柱 outline。"""
    return [[round(cx + r * ux, 3), round(cy + r * uy, 3)] for ux, uy in _OCT_DIRS]


def circle_px_to_meter(
    cx_px: float, cy_px: float, r_px: float, *,
    dpi: int, page_h_pt: float, scale_m_pt: float,
    origin_pt: tuple[float | None, float | None],
) -> tuple[float, float, float]:
    """像素圆心/半径 → 米坐标(与 _Ctx.to_m 同口径:px→pt→翻转平移比例)。

    `origin_pt` 的某个方向可能是 `None`（`_origin_pt` 用它区分「原点在 0」
    与「没找到原点」）。**按 0 兜底而不是抛异常**：抛出去会被
    `detect_pile_columns` 的 `except` 吞掉，**整张图的桩静默全丢**。
    与 `transform_from_geometry` 同口径。
    """
    pt_per_px = 72.0 / dpi
    cx_pt = cx_px * pt_per_px
    cy_pt = cy_px * pt_per_px
    r_pt = r_px * pt_per_px
    fx = cx_pt - float(origin_pt[0] or 0.0)
    fy = (page_h_pt - cy_pt) - float(origin_pt[1] or 0.0)
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


def _render_gray(pdf_bytes: bytes, dpi: int, src: str):
    """PDF 首页 → 灰度图(按像素预算降采样),返回 (gray, 实际 dpi)。

    **整段持 `_RENDER_LOCK`**：fitz 渲染与 cv2 转换都是原生代码，
    被超时放弃的僵尸线程会与主流程并发进入这里，实测导致 SIGSEGV。
    """
    import cv2
    import fitz
    import numpy as np

    with _RENDER_LOCK:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            page = doc[0]
            eff_dpi = dpi
            longest_px = max(page.rect.width, page.rect.height) * dpi / 72.0
            if longest_px > _MAX_RENDER_PX:
                eff_dpi = dpi * _MAX_RENDER_PX / longest_px
            budget_scale = render_scale_for(
                width_px=page.rect.width * eff_dpi / 72.0,
                height_px=page.rect.height * eff_dpi / 72.0,
            )
            if budget_scale < 1.0:
                eff_dpi *= budget_scale
                # 降级必须可见：少检出的桩要有据可查
                logger.info(
                    "[circle] %s 超像素预算(%.0f MP)，dpi 降到 %.0f"
                    "——桩检出会减少", src or "?",
                    page.rect.width * page.rect.height * (dpi / 72.0) ** 2 / 1e6,
                    eff_dpi)
            pix = page.get_pixmap(
                matrix=fitz.Matrix(eff_dpi / 72.0, eff_dpi / 72.0), alpha=False
            )
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, 3
            )
            # cvtColor 产出新数组，不再引用 pix 的缓冲，可安全越过 doc.close()
            return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY), eff_dpi
        finally:
            doc.close()


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

        gray, eff_dpi = _render_gray(pdf_bytes, dpi, src)
        if gray is None:
            return []

        dpi = eff_dpi  # 后续半径像素换算用实际渲染 dpi
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
