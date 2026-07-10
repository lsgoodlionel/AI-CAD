"""A-12 图纸切图预处理器（喂 VLM）。

对 A0/A1 大图做**确定性**切图，为下游 VLM 语义服务（A-11）准备输入：

- **标题栏裁剪**：图签通常位于图框右下角 / 右侧竖栏 / 底部横栏。用
  ``geometry_extractor`` 提取的文本框位置做密度启发式定位，裁出 title_block crop。
- **整图缩略图**：overview，最长边 ≤ 模型分辨率上限（默认 1568px，对齐 Claude；
  通用 VLM 亦在此量级），避免细节在模型端强制降采样中丢失。
- **可选切片**：原生分辨率远超上限的大图，额外产出高分辨率网格切片 tiles，
  供需要细节时按块喂入。

纯确定性，**不调用 VLM/LLM**。任何失败优雅降级——始终返回可用的 overview，
即使标题栏定位失败也不抛异常中断上层流程。

PDF 走 fitz 裁剪渲染（clip rect，锐利）；DXF 先渲染成图再按世界坐标映射裁剪。
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────
# 模型分辨率上限（最长边像素）：对齐 Claude 1568；通用 VLM 同量级
MAX_VLM_PX = 1568
RENDER_DPI = 150            # overview 渲染 DPI（上限内尽量清晰）
CROP_DPI = 200             # 标题栏裁剪渲染 DPI（局部要更清晰）
DXF_FIG_SIZE = (16, 12)    # 英寸
DXF_FIG_DPI = 110

# 标题栏定位阈值（归一化图像坐标，原点左上、y 向下、右下角=(1,1)）
MIN_TITLE_TEXTS = 4        # 触发密度定位的最少文本数
CORNER_X_MIN, CORNER_Y_MIN = 0.55, 0.62   # 右下角块
RIGHT_X_MIN = 0.72                          # 右侧竖栏
BOTTOM_Y_MIN = 0.80                         # 底部横栏
CROP_PAD = 0.015           # bbox 外扩比例
MIN_CROP_FRAC = 0.18       # 裁剪框最小边长（避免裁出细条）
MAX_CROP_FRAC = 0.92       # 超此视为定位失败（几乎整页）

# 默认右下角回退裁剪（定位失败时保底命中图签常见位置）
FALLBACK_X0, FALLBACK_Y0 = 0.62, 0.68

# 切片触发与网格上限
TILE_TRIGGER = 1.3         # 原生最长边 > 上限 × 此系数才切片
MAX_TILES_PER_AXIS = 3

Bbox = tuple[float, float, float, float]  # (x0, y0, x1, y1)，归一化 [0,1]


def _empty_result() -> dict:
    """完全失败时的占位（title_block/overview 均为空字节，tiles 为 None）。"""
    return {"title_block_png": b"", "overview_png": b"", "tiles": None}


def preprocess_for_vlm(data: bytes, ext: str) -> dict:
    """图纸切图预处理主入口（确定性，绝不抛异常）。

    参数：
        data: 原始文件字节。
        ext:  文件扩展名（``pdf`` / ``dxf`` / ``dwg``，大小写与前导点不敏感）。

    返回：
        ``{"title_block_png": bytes, "overview_png": bytes, "tiles": list[bytes] | None}``

        - ``title_block_png``：标题栏裁剪 PNG；定位失败时回退右下角固定比例裁剪；
          仍失败时为空字节。
        - ``overview_png``：整图缩略图 PNG，最长边 ≤ ``MAX_VLM_PX``。
        - ``tiles``：高分辨率网格切片（仅 PDF 大图产出），否则 ``None``。
    """
    normalized_ext = ext.lower().lstrip(".") if ext else ""
    try:
        if normalized_ext == "pdf":
            return _preprocess_pdf(data)
        if normalized_ext in ("dxf", "dwg"):
            return _preprocess_dxf(data, normalized_ext)
        logger.info("[vlm_preprocess] 不支持的扩展名，降级空结果: %s", normalized_ext)
        return _empty_result()
    except Exception as exc:  # noqa: BLE001 — 预处理失败必须降级，绝不中断上层
        logger.warning("[vlm_preprocess] 预处理失败 ext=%s: %s", normalized_ext, exc)
        return _empty_result()


# ── PDF 路径 ──────────────────────────────────────────────────

def _preprocess_pdf(data: bytes) -> dict:
    """PDF 首页：clip 渲染 overview + 标题栏裁剪 + 大图切片。"""
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if len(doc) == 0:
            return _empty_result()
        page = doc[0]
        page_w, page_h = float(page.rect.width), float(page.rect.height)
        if page_w <= 0 or page_h <= 0:
            return _empty_result()

        overview_png = _render_pdf_region(page, (0.0, 0.0, 1.0, 1.0), RENDER_DPI, MAX_VLM_PX)

        points = _pdf_norm_points(page, page_w, page_h)
        bbox = _locate_title_block(points) or _fallback_bbox()
        title_png = _render_pdf_region(page, bbox, CROP_DPI, MAX_VLM_PX)

        tiles = _pdf_tiles(page, page_w, page_h)
        return {
            "title_block_png": title_png,
            "overview_png": overview_png,
            "tiles": tiles,
        }
    finally:
        doc.close()


def _pdf_norm_points(page, page_w: float, page_h: float) -> list[tuple[float, float]]:
    """PDF 文本框中心 → 归一化图像坐标（top-left 原点，y 向下，与渲染一致）。"""
    from core.model3d.geometry_extractor import extract_pdf_geometry

    geom = extract_pdf_geometry(page.parent.tobytes())
    points: list[tuple[float, float]] = []
    for x, y, _content in geom.texts:
        nx, ny = x / page_w, y / page_h
        if 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0:
            points.append((nx, ny))
    return points


def _render_pdf_region(page, bbox: Bbox, dpi: int, cap: int) -> bytes:
    """渲染 page 的归一化子区域为 PNG（zoom 使最长边 ≤ cap）。"""
    import fitz

    x0, y0, x1, y1 = bbox
    rect = fitz.Rect(
        x0 * page.rect.width, y0 * page.rect.height,
        x1 * page.rect.width, y1 * page.rect.height,
    )
    zoom = dpi / 72.0
    longest = max(rect.width, rect.height) * zoom
    if longest > cap:
        zoom *= cap / longest
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
    return pix.tobytes("png")


def _pdf_tiles(page, page_w: float, page_h: float) -> list[bytes] | None:
    """原生分辨率远超上限时，产出高分辨率网格切片；否则 None。"""
    native_longest = max(page_w, page_h) * RENDER_DPI / 72.0
    if native_longest <= MAX_VLM_PX * TILE_TRIGGER:
        return None
    per_axis = min(MAX_TILES_PER_AXIS, _ceil_div(int(native_longest), MAX_VLM_PX))
    if per_axis < 2:
        return None
    tiles: list[bytes] = []
    step = 1.0 / per_axis
    for row in range(per_axis):
        for col in range(per_axis):
            bbox = (col * step, row * step, (col + 1) * step, (row + 1) * step)
            tiles.append(_render_pdf_region(page, bbox, RENDER_DPI, MAX_VLM_PX))
    return tiles


# ── DXF 路径 ──────────────────────────────────────────────────

def _preprocess_dxf(data: bytes, ext: str) -> dict:
    """DXF/DWG：渲染整图 → 世界坐标映射定位标题栏 → PIL 裁剪。"""
    if ext == "dwg":
        from core.ai_review.dwg_support import ensure_dxf

        data, converted_ext, warning = ensure_dxf(data, ext)
        if warning or converted_ext != "dxf":
            logger.info("[vlm_preprocess] DWG 转换降级: %s", warning)
            return _empty_result()

    overview_png, world_box = _render_dxf(data)
    if not overview_png or world_box is None:
        return {"title_block_png": b"", "overview_png": overview_png, "tiles": None}

    points = _dxf_norm_points(data, world_box)
    bbox = _locate_title_block(points) or _fallback_bbox()
    title_png = _crop_png(overview_png, bbox)
    return {"title_block_png": title_png, "overview_png": overview_png, "tiles": None}


def _render_dxf(data: bytes) -> tuple[bytes, tuple[float, float, float, float] | None]:
    """DXF → PNG（≤ 上限）+ 实际世界坐标包络框 (xmin,ymin,xmax,ymax)。"""
    import matplotlib
    matplotlib.use("Agg")
    import ezdxf
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    doc = ezdxf.read(io.StringIO(data.decode("utf-8", errors="ignore")))
    msp = doc.modelspace()
    fig = plt.figure(figsize=DXF_FIG_SIZE)
    ax = fig.add_axes([0, 0, 1, 1])
    try:
        Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(msp)
        ax.margins(0)
        xmin, xmax = ax.get_xlim()
        ymin, ymax = ax.get_ylim()
        ax.set_axis_off()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=DXF_FIG_DPI)
    finally:
        plt.close(fig)
    png, _w, _h = _cap_png(buffer.getvalue(), MAX_VLM_PX)
    if xmax <= xmin or ymax <= ymin:
        return png, None
    return png, (float(xmin), float(ymin), float(xmax), float(ymax))


def _dxf_norm_points(
    data: bytes, world_box: tuple[float, float, float, float]
) -> list[tuple[float, float]]:
    """DXF 文本世界坐标 → 归一化图像坐标（y 翻转，与图像 top-left 对齐）。"""
    from core.model3d.geometry_extractor import extract_dxf_geometry

    geom = extract_dxf_geometry(data)
    xmin, ymin, xmax, ymax = world_box
    span_x, span_y = xmax - xmin, ymax - ymin
    points: list[tuple[float, float]] = []
    for x, y, _content in geom.texts:
        nx = (x - xmin) / span_x
        ny = 1.0 - (y - ymin) / span_y  # 世界 y 向上 → 图像 y 向下
        if 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0:
            points.append((nx, ny))
    return points


# ── 标题栏定位（归一化图像坐标，纯确定性启发式）──────────────

def _locate_title_block(points: list[tuple[float, float]]) -> Bbox | None:
    """按文本密度定位标题栏 bbox；命中右下角/右栏/底栏之一，否则 None。

    优先级：右下角块 > 右侧竖栏 > 底部横栏（图签在右下角最常见）。
    """
    valid = [(nx, ny) for nx, ny in points if 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0]
    if len(valid) < MIN_TITLE_TEXTS:
        return None

    corner = [p for p in valid if p[0] >= CORNER_X_MIN and p[1] >= CORNER_Y_MIN]
    right = [p for p in valid if p[0] >= RIGHT_X_MIN]
    bottom = [p for p in valid if p[1] >= BOTTOM_Y_MIN]

    for cluster in (corner, right, bottom):
        if len(cluster) >= MIN_TITLE_TEXTS:
            bbox = _tight_bbox(cluster)
            return bbox if bbox is not None else None
    return None


def _tight_bbox(cluster: list[tuple[float, float]]) -> Bbox | None:
    """文本簇紧包围盒 → 外扩 + 最小边长 + 上限校验；过大视为失败返回 None。"""
    xs = [p[0] for p in cluster]
    ys = [p[1] for p in cluster]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    x0, y0 = x0 - CROP_PAD, y0 - CROP_PAD
    x1, y1 = x1 + CROP_PAD, y1 + CROP_PAD
    x0, y0, x1, y1 = _expand_to_min(x0, y0, x1, y1)
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(1.0, x1), min(1.0, y1)
    if (x1 - x0) >= MAX_CROP_FRAC and (y1 - y0) >= MAX_CROP_FRAC:
        return None  # 几乎整页 → 定位无效
    if x1 <= x0 or y1 <= y0:
        return None
    return (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4))


def _expand_to_min(x0: float, y0: float, x1: float, y1: float) -> Bbox:
    """裁剪框边长不足 MIN_CROP_FRAC 时，绕中心对称扩张。"""
    if (x1 - x0) < MIN_CROP_FRAC:
        cx = (x0 + x1) / 2
        x0, x1 = cx - MIN_CROP_FRAC / 2, cx + MIN_CROP_FRAC / 2
    if (y1 - y0) < MIN_CROP_FRAC:
        cy = (y0 + y1) / 2
        y0, y1 = cy - MIN_CROP_FRAC / 2, cy + MIN_CROP_FRAC / 2
    return x0, y0, x1, y1


def _fallback_bbox() -> Bbox:
    """定位失败时的右下角固定比例裁剪（保底命中常见图签位置）。"""
    return (FALLBACK_X0, FALLBACK_Y0, 1.0, 1.0)


# ── 图像工具 ──────────────────────────────────────────────────

def _crop_png(png: bytes, bbox: Bbox) -> bytes:
    """按归一化 bbox 裁剪 PNG（PIL），失败返回空字节。"""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(png))
        width, height = image.size
        x0, y0, x1, y1 = bbox
        box = (
            int(x0 * width), int(y0 * height),
            max(int(x1 * width), int(x0 * width) + 1),
            max(int(y1 * height), int(y0 * height) + 1),
        )
        cropped = image.crop(box)
        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 — 裁剪失败降级空字节
        logger.warning("[vlm_preprocess] 标题栏裁剪失败: %s", exc)
        return b""


def _cap_png(png: bytes, cap: int) -> tuple[bytes, int, int]:
    """最长边超 cap 时等比缩放（PIL）；返回 (png, width, height)。"""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(png))
        width, height = image.size
        longest = max(width, height)
        if longest > cap:
            ratio = cap / longest
            image = image.resize((max(1, int(width * ratio)), max(1, int(height * ratio))))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue(), image.width, image.height
        return png, width, height
    except Exception as exc:  # noqa: BLE001 — 缩放失败原样返回
        logger.warning("[vlm_preprocess] overview 缩放失败: %s", exc)
        return png, 0, 0


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)
