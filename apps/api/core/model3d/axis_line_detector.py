"""图纸候选轴线抽取(供人工标定「直接选图上那条线」用)。

人工标定的第一种形式:打开已导入的图纸,**照着图纸内容直接选中某一条线**并命名。
要能「选中」,先得知道图上有哪些线可选——本模块给出候选直线列表(归一化坐标),
前端点哪儿就吸附到最近的候选线,人只管命名,不用手描端点。

难点是轴线通常画成**点划线**,栅格检测会碎成一堆短段;因此按「位置聚合 + 跨度合并」
把同一条轴线的碎段并回一条,并按跨度筛掉标注线/引出线。

分层(照 circle_detector 的约定):
- classify_line / merge_collinear / filter_by_span：纯几何,离线可测
- detect_lines_px：cv2.HoughLinesP 薄封装
- detect_axis_line_candidates：栅格化 PDF → 候选线(IO,优雅降级返回 [])
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DEFAULT_DPI = 100            # 选线只需位置,不需桩检测那样的高分辨率
_MAX_RENDER_PX = 6000
STRAIGHT_TOL = 0.01          # 归一化偏移容差:超出即不算横平竖直
MERGE_TOL = 0.004            # 同位置线合并容差(点划线碎段归并)
MIN_SPAN = 0.25              # 候选线最小跨度(占页高比例),滤掉标注/引出线
MAX_CANDIDATES = 120         # 单图候选上限,防噪声图刷爆前端


def classify_line(
    x1: float, y1: float, x2: float, y2: float, tol: float = STRAIGHT_TOL,
) -> str | None:
    """线段 → 'x'(竖向轴线)| 'y'(横向轴线)| None(斜线,不作候选)。"""
    dx, dy = abs(x2 - x1), abs(y2 - y1)
    if dx <= tol and dy > tol:
        return "x"
    if dy <= tol and dx > tol:
        return "y"
    return None


def _position(line: dict) -> float:
    """线所在的位置坐标:竖线取 x,横线取 y。"""
    if line["direction"] == "x":
        return (line["x1_norm"] + line["x2_norm"]) / 2
    return (line["y1_norm"] + line["y2_norm"]) / 2


def _span(line: dict) -> float:
    """线的延伸长度(竖线取 y 跨度,横线取 x 跨度)。"""
    if line["direction"] == "x":
        return abs(line["y2_norm"] - line["y1_norm"])
    return abs(line["x2_norm"] - line["x1_norm"])


def merge_collinear(lines: list[dict], tol: float = MERGE_TOL) -> list[dict]:
    """把同方向同位置的碎段并成一条(点划线轴线必需),跨度取并集包络。"""
    out: list[dict] = []
    for direction in ("x", "y"):
        group = sorted(
            (ln for ln in lines if ln["direction"] == direction), key=_position
        )
        bucket: list[dict] = []
        for ln in group:
            if bucket and _position(ln) - _position(bucket[-1]) <= tol:
                bucket.append(ln)
                continue
            if bucket:
                out.append(_envelope(bucket))
            bucket = [ln]
        if bucket:
            out.append(_envelope(bucket))
    return out


def _envelope(bucket: list[dict]) -> dict:
    """一组碎段 → 一条线:位置取均值,延伸方向取最小~最大包络。"""
    direction = bucket[0]["direction"]
    pos = sum(_position(ln) for ln in bucket) / len(bucket)
    if direction == "x":
        lo = min(min(ln["y1_norm"], ln["y2_norm"]) for ln in bucket)
        hi = max(max(ln["y1_norm"], ln["y2_norm"]) for ln in bucket)
        return {"direction": "x", "x1_norm": pos, "y1_norm": lo,
                "x2_norm": pos, "y2_norm": hi}
    lo = min(min(ln["x1_norm"], ln["x2_norm"]) for ln in bucket)
    hi = max(max(ln["x1_norm"], ln["x2_norm"]) for ln in bucket)
    return {"direction": "y", "x1_norm": lo, "y1_norm": pos,
            "x2_norm": hi, "y2_norm": pos}


def filter_by_span(lines: list[dict], min_span: float = MIN_SPAN) -> list[dict]:
    """只留贯通性够长的线——轴线贯穿全图,标注线/引出线短。"""
    return [ln for ln in lines if _span(ln) >= min_span]


def build_candidates(
    raw: list[tuple[float, float, float, float]], *,
    min_span: float = MIN_SPAN, limit: int = MAX_CANDIDATES,
) -> list[dict]:
    """原始线段(归一化)→ 候选轴线:判方向 → 并碎段 → 筛跨度 → 长者优先截断。"""
    typed = []
    for x1, y1, x2, y2 in raw:
        direction = classify_line(x1, y1, x2, y2)
        if direction:
            typed.append({"direction": direction, "x1_norm": x1, "y1_norm": y1,
                          "x2_norm": x2, "y2_norm": y2})
    merged = filter_by_span(merge_collinear(typed), min_span)
    merged.sort(key=_span, reverse=True)
    picked = merged[:limit]
    for ln in picked:
        for k in ("x1_norm", "y1_norm", "x2_norm", "y2_norm"):
            ln[k] = round(ln[k], 5)
    return picked


def detect_lines_px(gray_image, min_len_px: int) -> list[tuple[float, ...]]:
    """cv2.HoughLinesP 薄封装;返回 [(x1,y1,x2,y2)] 像素坐标。cv2 缺失 → []。"""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    edges = cv2.Canny(gray_image, 50, 150, apertureSize=3)
    segs = cv2.HoughLinesP(
        edges, rho=1, theta=3.14159 / 180, threshold=80,
        minLineLength=min_len_px, maxLineGap=max(min_len_px // 4, 8),
    )
    if segs is None:
        return []
    # cv2 版本间形状不一((N,1,4) 或 (N,4)),统一摊平取前 4 个数
    out: list[tuple[float, ...]] = []
    for s in segs:
        flat = [float(v) for v in np.asarray(s).reshape(-1)]
        if len(flat) >= 4:
            out.append(tuple(flat[:4]))
    return out


def detect_axis_line_candidates(
    pdf_bytes: bytes, *, dpi: int = DEFAULT_DPI, min_span: float = MIN_SPAN,
) -> list[dict]:
    """栅格化 PDF → 候选轴线(归一化坐标,同除页高,与人工标定入库口径一致)。

    任何依赖缺失/异常一律返回 []（优雅降级:选不出候选就退回手描两点)。
    """
    try:
        import cv2
        import fitz
        import numpy as np

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            page = doc[0]
            eff_dpi = dpi
            longest_px = max(page.rect.width, page.rect.height) * dpi / 72.0
            if longest_px > _MAX_RENDER_PX:
                eff_dpi = dpi * _MAX_RENDER_PX / longest_px
            pix = page.get_pixmap(
                matrix=fitz.Matrix(eff_dpi / 72.0, eff_dpi / 72.0), alpha=False
            )
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, 3
            )
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            h_px = pix.height
        finally:
            doc.close()

        if h_px <= 0:
            return []
        segs = detect_lines_px(gray, int(min_span * h_px * 0.6))
        raw = [(x1 / h_px, y1 / h_px, x2 / h_px, y2 / h_px)
               for x1, y1, x2, y2 in segs]
        return build_candidates(raw, min_span=min_span)
    except Exception as exc:  # noqa: BLE001 — 候选线抽取失败降级为手描
        logger.warning("候选轴线抽取失败,退回手描两点: %s", exc)
        return []
