"""渲染分辨率：按图纸**自己的比例**定 DPI，用像素预算兜底。

## 为什么不能用一个全库常数

`export_yolo_dataset.py` 一直用 `DPI = 100`。算一下一根 0.6m 见方的柱
在图上有多大：

    比例      图上尺寸    DPI=100    DPI=150    DPI=200
    1:50      12.00mm      47.2px     70.9px     94.5px
    1:100      6.00mm      23.6px     35.4px     47.2px
    1:150      4.00mm     **15.7px**  23.6px     31.5px
    1:200      3.00mm      11.8px     17.7px     23.6px
    1:300      2.00mm       7.9px     11.8px     15.7px

YOLO 可靠检测下限约 16~20px。**而歌剧院的主力比例正是 1:150**
（实测 1068 张，全库最多）—— 旧常数下它的柱只有 15.7px，压在下限上。
`DPI = 100` 是按 1:100 定的，对主力比例不够。

这是 `CLAUDE.md` 记的第一条教训：**渲染分辨率要匹配问题所在的尺度**。
比例批栽在同一件事上（1872×1324 缩到 420px，判据要用的门只剩 7px）。

## 推导，不是拍脑袋

要让尺寸 `size_m` 的构件至少占 `target_px` 像素：

    (size_m × 1000 / denom) / 25.4 × dpi ≥ target_px
    ⇒ dpi ≥ target_px × 25.4 / (size_m × 1000) × denom

取 `size_m=0.6`（柱的典型下限档）、`target_px=24`（YOLO 下限 16~20 之上
留一点余量），系数 ≈ **1.016** —— 也就是 **DPI 约等于比例分母**。

## 上限

放大到多少都不嫌够，但内存与耗时不答应。像素预算与
`circle_detector.render_scale_for` 同一条纪律：超预算就降，并且**说出来**
（降级必须可见）。那边取 2 MP 是因为「76 秒的结果会被 20 秒超时丢弃」；
这里是离线导出训练集，没有那个超时，所以给到 12 MP。
"""
from __future__ import annotations

import math

#: 目标：让这个尺寸的构件至少占 `TARGET_PX` 像素。
#: 0.6m 是柱的典型下限档（`_COLUMN_SIZE` 窗口是 0.2~1.5m，
#: 0.6 是实测最常见的一档）。
TARGET_ELEMENT_M = 0.6

#: YOLO 对小目标的可靠检测下限约 16~20px，取 24 留余量。
TARGET_PX = 24.0

#: 离线导出训练集的像素预算。比 `circle_detector` 的 2 MP 宽 ——
#: 那边卡在 20 秒识别超时上，这里没有那个约束。
MAX_RENDER_MEGAPIXELS = 12.0

#: 比例缺不出来时的兜底。全库实测只有 **69.6%** 的图能解析出合法比例分母
#: （见 `scale_evidence`），剩下三成必须有值可用 —— 缺失不得阻断。
FALLBACK_DPI = 100.0

#: 再小的详图也不必低于它（1:50 及更大比例本来就够清晰）。
MIN_DPI = 100.0


def element_px_at(size_m: float, denom: float, dpi: float) -> float:
    """尺寸 `size_m` 的构件，在 1:`denom` 的图上按 `dpi` 渲染后占多少像素。"""
    mm_on_paper = size_m * 1000.0 / max(float(denom), 1e-9)
    return mm_on_paper / 25.4 * float(dpi)


def _is_usable(denom) -> bool:
    """比例分母可用吗 —— None / 0 / 负数 / NaN 一律不可用。"""
    try:
        value = float(denom)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and value > 0


def dpi_for_scale(
    denom,
    *,
    page_w_pt: float | None = None,
    page_h_pt: float | None = None,
    target_px: float = TARGET_PX,
    return_capped: bool = False,
):
    """按比例分母算渲染 DPI；给了页面尺寸就用像素预算兜住上限。

    `return_capped=True` 时返回 `(dpi, 是否被预算压低)` —— 调用方据此
    记日志。**降级必须可见**：静默压低分辨率会让「这张图为什么检不出」
    查无对证。
    """
    if not _is_usable(denom):
        dpi = FALLBACK_DPI
    else:
        need = target_px * 25.4 / (TARGET_ELEMENT_M * 1000.0) * float(denom)
        dpi = max(need, MIN_DPI)

    capped = False
    if page_w_pt and page_h_pt and page_w_pt > 0 and page_h_pt > 0:
        # 页面点 → 像素：px = pt / 72 * dpi
        pixels = (page_w_pt / 72.0 * dpi) * (page_h_pt / 72.0 * dpi)
        budget = MAX_RENDER_MEGAPIXELS * 1e6
        if pixels > budget:
            dpi *= (budget / pixels) ** 0.5
            capped = True

    dpi = round(dpi, 1)
    return (dpi, capped) if return_capped else dpi


def render_clip(page, clip, dpi: float):
    """按任意（浮点）DPI 渲染页面的一块，返回 `fitz.Pixmap`。

    **不要用 `page.get_pixmap(dpi=…)`**：它只收整数，而本模块与
    `gold.batch_design.render_dpi_for_crop` 算出来的都是浮点。传浮点直接抛
    `TypeError`，外面若有宽泛 `except`，失败就是静默的 —— col3 批扫了
    7520 个候选、出 0 格、退出码 0，就是这样发生的。

    缩放矩阵收浮点，渲染尺寸也就是算出来的那个，不被取整。
    """
    import fitz  # type: ignore[import-untyped]  # 懒加载：本模块其余部分是纯函数

    zoom = float(dpi) / 72.0
    return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
