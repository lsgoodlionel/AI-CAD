"""图纸栅格化(供标注类交互统一在位图上进行)。

**为什么**:轴线标定、图框字段框选这类交互要在图上点/拖,必须有一张位图。
PDF 原本走 iframe 内嵌浏览器渲染,拿不到坐标,所以标定只能用图片格式——
这里把 PDF 也渲成 PNG,让三种格式(PDF / 图片 / CAD)走同一条标注路径。

**坐标口径(关键)**:渲染**等比缩放**,故
`像素 / 图片高度 == 点 / 页面高度`,前端「同除显示高度」得到的归一化坐标
与后端 `page_h` 口径完全一致,无需额外换算。等比是硬要求,一旦拉伸就对不上了。

渲染结果写回 MinIO 缓存(同 key 幂等),避免每次打开都重渲。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 标注用分辨率:够看清图框小字,又不至于让 A0 图爆内存
RASTER_DPI = 150
#: 渲染最长边上限(px);超出按比例降 dpi——**等比**降,不改变宽高比。
#:
#: 12000 是实测定的:5084×2412pt 的超大图在旧上限 6000 下被降到 84dpi,
#: 图框「专业」二字只剩 7px,人看不清、也框不准。线稿 PNG 压缩极好——
#: 该图 200dpi 也只有 1.5MB、3.2s,提高上限的代价远小于「看不清」的代价。
MAX_RASTER_PX = 12000


def raster_key(project_id: str, drawing_id: str) -> str:
    """PDF 栅格图的存储 key(与 CAD 预览资产分开,避免互相覆盖)。"""
    return f"projects/{project_id}/raster/{drawing_id}.png"


def effective_dpi(page_w_pt: float, page_h_pt: float, dpi: int = RASTER_DPI) -> int:
    """按最长边上限折算实际 dpi(等比,不改宽高比)。"""
    longest = max(page_w_pt, page_h_pt) * dpi / 72.0
    if longest <= MAX_RASTER_PX or longest <= 0:
        return dpi
    return max(int(dpi * MAX_RASTER_PX / longest), 36)


def render_pdf_png(pdf_bytes: bytes, dpi: int = RASTER_DPI) -> bytes | None:
    """PDF 首页 → PNG 字节(等比)。失败返回 None(调用方降级)。"""
    try:
        import fitz

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if doc.page_count < 1:
                return None
            page = doc[0]
            eff = effective_dpi(float(page.rect.width), float(page.rect.height), dpi)
            return page.get_pixmap(dpi=eff).tobytes("png")
    except Exception as exc:  # noqa: BLE001 — 渲染失败降级为不可标注
        logger.warning("[raster] PDF 栅格化失败: %s", exc)
        return None


def ensure_pdf_raster(project_id: str, drawing_id: str, file_key: str) -> str | None:
    """确保 PDF 栅格图存在于对象存储,返回其 key;失败 → None。

    幂等:已存在直接返回,不重复渲染。
    """
    from core.storage import get_file_bytes, object_exists, upload_file

    key = raster_key(project_id, drawing_id)
    try:
        if object_exists(key):
            return key
        png = render_pdf_png(get_file_bytes(file_key))
        if png is None:
            return None
        upload_file(png, key, "image/png")
        return key
    except Exception as exc:  # noqa: BLE001
        logger.warning("[raster] 栅格图写入失败 %s: %s", drawing_id, exc)
        return None


# ── CAD 栅格化(等比,专供标注)────────────────────────────────────

#: 标注用 CAD 渲染最长边(英寸 × dpi 后的像素上限由 MAX_RASTER_PX 兜住)
CAD_LONG_EDGE_IN = 16.0
CAD_DPI = 100


def cad_figsize(extent_w: float, extent_h: float) -> tuple[float, float]:
    """按图形实际范围算等比画布尺寸(英寸)。

    建模用的预览图是**固定画布** `(16,12)`,会把图拉伸——那对贴图无所谓,
    但**标注不行**:一旦宽高比被改，前端「同除显示高度」的归一化坐标就对不上
    图纸坐标系。故标注专用一张按范围等比渲染的图。
    """
    if extent_w <= 0 or extent_h <= 0:
        return (CAD_LONG_EDGE_IN, CAD_LONG_EDGE_IN * 0.75)
    if extent_w >= extent_h:
        return (CAD_LONG_EDGE_IN, CAD_LONG_EDGE_IN * extent_h / extent_w)
    return (CAD_LONG_EDGE_IN * extent_w / extent_h, CAD_LONG_EDGE_IN)


def render_dxf_png(data: bytes, file_ext: str = "dxf") -> bytes | None:
    """DXF/DWG → PNG(按图形范围**等比**)。失败返回 None。

    DWG 是二进制格式,ezdxf 读不了,先经 `dwg_support.ensure_dxf` 转换
    (LibreDWG dwg2dxf / ODA File Converter)。
    """
    import io
    import tempfile

    path = ""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import ezdxf
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

        from core.ai_review.dwg_support import ensure_dxf
        dxf_bytes, _resolved_ext, warning = ensure_dxf(data, file_ext)
        if warning:
            logger.warning("[raster] DWG 转换提示: %s", warning)

        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
            tmp.write(dxf_bytes)
            path = tmp.name
        doc = ezdxf.readfile(path)
        msp = doc.modelspace()

        fig = plt.figure()
        ax = fig.add_axes([0, 0, 1, 1])
        Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(msp)
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        fig.set_size_inches(*cad_figsize(abs(x1 - x0), abs(y1 - y0)))
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=CAD_DPI)
        plt.close(fig)
        return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 — 渲染失败降级为不可标注
        logger.warning("[raster] DXF 栅格化失败: %s", exc)
        return None
    finally:
        if path:
            try:
                import os
                os.unlink(path)
            except OSError:
                pass


def ensure_cad_raster(project_id: str, drawing_id: str, file_key: str) -> str | None:
    """确保 CAD 标注用等比栅格图存在,返回其 key;失败 → None(幂等)。"""
    from core.storage import get_file_bytes, object_exists, upload_file

    key = raster_key(project_id, drawing_id)
    try:
        if object_exists(key):
            return key
        ext = file_key.rsplit(".", 1)[-1].lower() if "." in file_key else "dxf"
        png = render_dxf_png(get_file_bytes(file_key), ext)
        if png is None:
            return None
        upload_file(png, key, "image/png")
        return key
    except Exception as exc:  # noqa: BLE001
        logger.warning("[raster] CAD 栅格图写入失败 %s: %s", drawing_id, exc)
        return None
