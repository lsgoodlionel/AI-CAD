"""标准资料 → 统一页文本（三条来路：PDF 文本层 / 扫描 OCR / EPUB）。

**为什么不直接用 `page.get_text()`**：图集是多栏排版，PDF 的提取顺序不是
阅读顺序 —— 实测 22G101-1 第 12 页按提取顺序读出来是
「总则 | 芯柱的根部标高… | 一栏中；除此之外…」，三个栏的句子互相穿插。
这里统一复用 `core.model3d.reading_order`（工程图说明栏恢复，已在生产验证）。

**为什么图集一律重新 OCR**：8 本图集自带文本层，但实测 22G101-1 第 8 页是
`'=眩目刑图罩坝'` 级乱码 —— 那是别人 OCR 过一遍的残次品。原文本层不采信，
只在 `text_layer_sample` 里留一份供人对照。

**侧边栏不丢弃而是升格为章节标识**：图集每页左右有竖排的章节条
（「总则」「平法制图规则」）。按提取顺序它会串进正文，但它本身是
**本页归属哪一章**的直接证据，比从正文猜章节可靠。判据是 bbox 高宽比。
"""
from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import fitz

from core.model3d.reading_order import (
    detect_columns, merge_into_lines, sort_by_reading_order,
)

logger = logging.getLogger(__name__)

#: 渲染 DPI。实测 200 与 300 的识别结果无实质差异（68 vs 65 token、
#: 置信度同为 0.98），而 200 更快 —— 取 200。
OCR_DPI = 200

#: 竖排侧边栏判据：bbox 高/宽 超过此比值即认定为竖排。
#: 图集侧边章节条实测 h/w 在 15 以上；正文行是横排（h/w < 1）。
SIDEBAR_ASPECT_MIN = 3.0

#: 侧边栏还必须贴边 —— 距页面左右边缘不超过页宽的这个比例。
#: 只用高宽比会误伤正文里的单列窄表格。
SIDEBAR_MARGIN_RATIO = 0.12

#: 低于此置信度的 OCR token 记入 `low_conf_tokens`，供人工核对。
#: 不丢弃 —— 丢了就再也不知道哪里识别得差。
LOW_CONFIDENCE = 0.60


#: 除换行与制表符外的控制字符一律剔除。
#: **实测**：闾成德书的 PDF 文本层里夹着 7 个 NUL（U+0000），
#: 它能一路穿过 Markdown 写入，直到入库那一步才炸
#: （`invalid byte sequence for encoding "UTF8": 0x00`）。
#: 在抽取口清掉，比在每个下游各挡一次可靠。
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize(text: str) -> str:
    """去掉不可打印控制字符，保留换行与制表。"""
    return _CONTROL_RE.sub("", text or "")


@dataclass(frozen=True)
class PageText:
    """一页（或 EPUB 一节）的抽取结果。"""

    index: int                              # 0-based
    text: str                               # 已按阅读顺序重排的正文
    method: str                             # text_layer / ocr / epub
    token_count: int = 0
    confidence: float | None = None         # OCR 平均置信；非 OCR 为 None
    sidebar: tuple[str, ...] = ()           # 竖排章节标识
    low_conf_tokens: tuple[str, ...] = ()   # 置信度低于阈值的片段
    warnings: tuple[str, ...] = ()
    extras: dict = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.text)


# --------------------------------------------------------------------------
# 通用：token → 正文
# --------------------------------------------------------------------------

def _split_sidebar(tokens: list[dict], page_w: float,
                   page_h: float) -> tuple[list[dict], list[str]]:
    """分出竖排侧边栏。返回 (正文 token, 侧边栏文字)。"""
    if page_w <= 0 or page_h <= 0:
        return tokens, []
    body: list[dict] = []
    side: list[str] = []
    margin = page_w * SIDEBAR_MARGIN_RATIO
    for t in tokens:
        bbox = t.get("bbox")
        if not bbox or len(bbox) < 4:
            body.append(t)
            continue
        w = max(bbox[2] - bbox[0], 1e-6)
        h = bbox[3] - bbox[1]
        near_edge = bbox[0] < margin or bbox[2] > page_w - margin
        if h / w >= SIDEBAR_ASPECT_MIN and near_edge:
            text = (t.get("text") or "").strip()
            if text:
                side.append(text)
            continue
        body.append(t)
    return body, side


def _clean_sidebar(side: list[str]) -> list[str]:
    """侧边栏去噪：只留**复现**的条目。

    图集的侧边章节条上下印多遍（实测「平法制图规则」一页出现 4 次），
    而竖排文字被横读产生的乱码（实测 `'法特医夫贝污制图夫贝二污制图…'`）
    每次都不一样、只出现一次 —— 复现性正是两者的分界。
    单条侧边栏时无从判别，原样保留而不是丢掉。
    """
    if len(side) <= 1:
        return side
    counts: dict[str, int] = {}
    for s in side:
        counts[s] = counts.get(s, 0) + 1
    repeated = [s for s, n in counts.items() if n > 1]
    return sorted(repeated or set(side), key=lambda s: (-counts[s], s))


def _tokens_to_text(tokens: list[dict], page_w: float, page_h: float, *,
                    keep_source_order: bool = False) -> tuple[str, list[str]]:
    """token → (正文, 侧边栏)。

    `keep_source_order=True` 时保留传入顺序 —— 用于**单栏**的 PDF 文本层：
    PDF 自身的 span 顺序已是阅读顺序，再按坐标重排反而会出错
    （实测教材页重排后中文标点被甩到句首：`1.？工程图采用的…`，
    原因是句末标点的 span bbox 起点比正文字符更靠左）。
    """
    body, side = _split_sidebar(tokens, page_w, page_h)
    ordered = body if keep_source_order else sort_by_reading_order(body)
    lines = merge_into_lines(ordered)
    body_text = "\n".join(line for line in lines if line.strip())
    return sanitize(body_text), [sanitize(x) for x in _clean_sidebar(side)]


# --------------------------------------------------------------------------
# 来路一：PDF 文本层
# --------------------------------------------------------------------------

def _page_tokens_from_text_layer(page: fitz.Page) -> list[dict]:
    """文本层 → token（span 粒度，带 bbox）。"""
    out: list[dict] = []
    data = page.get_text("dict")
    for block in data.get("blocks", ()):
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                text = (span.get("text") or "").strip()
                if not text:
                    continue
                out.append({"text": text, "bbox": list(span.get("bbox", ()))})
    return out


def extract_text_layer(path: Path) -> Iterator[PageText]:
    doc = fitz.open(path)
    try:
        for i, page in enumerate(doc):
            tokens = _page_tokens_from_text_layer(page)
            rect = page.rect
            columns = len(detect_columns(tokens))
            if columns <= 1:
                # **单栏直接用 PyMuPDF 原生顺序**：它按 block→line→span 输出，
                # 已经是阅读顺序。走坐标重排反而更差 —— 实测教材页重排后
                # 行被按 y 容差(3pt)拆碎，句末标点甩到下一句开头：
                # `3.8，8？\n用三个互相垂直的平面…`。
                text = sanitize(page.get_text("text").strip())
                side: list[str] = []
            else:
                text, side = _tokens_to_text(tokens, rect.width, rect.height)
            yield PageText(index=i, text=text, method="text_layer",
                           token_count=len(tokens), sidebar=tuple(side),
                           extras={"columns": columns})
    finally:
        doc.close()


# --------------------------------------------------------------------------
# 来路二：扫描件 OCR
# --------------------------------------------------------------------------

_OCR_ENGINE = None


def _ocr_engine():
    """惰性加载 RapidOCR。缺依赖时抛出 —— OCR 是本管线的硬依赖，
    静默降级会让整批资料默默变成空文件。"""
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr import RapidOCR
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _poly_to_bbox(box) -> list[float]:
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return [min(xs), min(ys), max(xs), max(ys)]


def ocr_page(page: fitz.Page, *, dpi: int = OCR_DPI) -> tuple[list[dict], list[float]]:
    """渲染并识别一页。返回 (token 列表[bbox 已换算为页面点], 置信度列表)。"""
    import numpy as np

    pix = page.get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    elif pix.n == 1:
        img = np.repeat(img, 3, axis=2)

    result = _ocr_engine()(img)
    if result is None or not getattr(result, "txts", None):
        return [], []

    scale = 72.0 / dpi          # 图像像素 → 页面点
    tokens: list[dict] = []
    scores: list[float] = []
    boxes = getattr(result, "boxes", None)
    txts = list(result.txts)
    confs = list(getattr(result, "scores", None) or [1.0] * len(txts))
    for k, text in enumerate(txts):
        text = (text or "").strip()
        if not text:
            continue
        if boxes is not None and k < len(boxes):
            bbox = [v * scale for v in _poly_to_bbox(boxes[k])]
        else:
            bbox = []
        conf = float(confs[k]) if k < len(confs) else 1.0
        tokens.append({"text": text, "bbox": bbox, "confidence": conf})
        scores.append(conf)
    return tokens, scores


def extract_ocr(path: Path, *, dpi: int = OCR_DPI,
                page_range: tuple[int, int] | None = None) -> Iterator[PageText]:
    doc = fitz.open(path)
    try:
        start, stop = page_range or (0, doc.page_count)
        for i in range(start, min(stop, doc.page_count)):
            page = doc[i]
            tokens, scores = ocr_page(page, dpi=dpi)
            rect = page.rect
            text, side = _tokens_to_text(tokens, rect.width, rect.height)
            low = tuple(t["text"] for t in tokens
                        if t.get("confidence", 1.0) < LOW_CONFIDENCE)
            mean_conf = sum(scores) / len(scores) if scores else None
            warnings: list[str] = []
            if not tokens:
                warnings.append("ocr_empty")
            yield PageText(index=i, text=text, method="ocr",
                           token_count=len(tokens), confidence=mean_conf,
                           sidebar=tuple(side), low_conf_tokens=low,
                           warnings=tuple(warnings),
                           extras={"dpi": dpi})
    finally:
        doc.close()


# --------------------------------------------------------------------------
# 来路三：EPUB
# --------------------------------------------------------------------------

_HTML_SUFFIXES = (".html", ".xhtml", ".htm")
_WS_RE = re.compile(r"[ \t ]+")
_BLANK_RE = re.compile(r"\n{3,}")


def _epub_spine(zf: zipfile.ZipFile) -> list[str]:
    """按 OPF spine 顺序返回 HTML 条目；拿不到 OPF 就退回文件名自然序。

    **不能用 `namelist()` 的默认顺序**：chapter100 会排在 chapter11 前面，
    整本书的章节顺序会乱。
    """
    try:
        from bs4 import BeautifulSoup

        opf = next(n for n in zf.namelist() if n.lower().endswith(".opf"))
        soup = BeautifulSoup(zf.read(opf), "xml")
        base = opf.rsplit("/", 1)[0] + "/" if "/" in opf else ""
        href_by_id = {
            item.get("id"): item.get("href")
            for item in soup.find_all("item") if item.get("id")
        }
        names = zf.namelist()
        out: list[str] = []
        for ref in soup.find_all("itemref"):
            href = href_by_id.get(ref.get("idref"))
            if not href:
                continue
            full = base + href
            if full in names:
                out.append(full)
        if out:
            return out
    except Exception as exc:                      # noqa: BLE001 - 退回自然序即可
        logger.warning("EPUB spine 解析失败，退回文件名排序：%s", exc)

    def natural(name: str) -> tuple:
        return tuple(int(p) if p.isdigit() else p
                     for p in re.split(r"(\d+)", name))

    return sorted((n for n in zf.namelist()
                   if n.lower().endswith(_HTML_SUFFIXES)), key=natural)


def extract_epub(path: Path) -> Iterator[PageText]:
    from bs4 import BeautifulSoup

    with zipfile.ZipFile(path) as zf:
        for i, name in enumerate(_epub_spine(zf)):
            soup = BeautifulSoup(zf.read(name), "html.parser")
            images = [img.get("src") for img in soup.find_all("img") if img.get("src")]
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = _BLANK_RE.sub("\n\n", _WS_RE.sub(" ", soup.get_text("\n")))
            text = sanitize(
                "\n".join(ln.strip() for ln in text.splitlines() if ln.strip()))
            yield PageText(index=i, text=text, method="epub",
                           token_count=len(text.split()),
                           extras={"href": name, "images": images})


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------

def extract(source, **kwargs) -> Iterator[PageText]:
    """按 `KnowledgeSource.extract_method` 分派。未知来路直接报错，
    不猜 —— 猜错会产出一整本空文件而无人察觉。"""
    method = source.extract_method
    if method == "text_layer":
        return extract_text_layer(source.path)
    if method == "ocr":
        return extract_ocr(source.path, **kwargs)
    if method == "epub":
        return extract_epub(source.path)
    raise ValueError(f"未知抽取方式 {method!r}（{source.key}）")
