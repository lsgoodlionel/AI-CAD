"""资料 → 插图/表格清单（训练标记数据的第一层：图 + 题注弱标签）。

**题注就是标签**。中文工程书的插图一律带「图1-15 汉字图例」「表8-1 常用构件代号」
这样的题注，它比任何自动分类都准 —— 是作者写下的、关于这张图画的是什么的
第一手陈述。本模块的核心工作就是把**图与它的题注正确配对**。

三条来路，可信度依次递减，`caption_source` 如实记录用了哪条：
1. `epub_grap` —— EPUB 的 `<div class="pic"><p class="grap">题注</p><img></div>`，
   题注与图在同一容器里，配对**无歧义**；
2. `pdf_nearby` —— PDF 内嵌图 + 页面上距其最近的题注行（有歧义，记距离）；
3. `page_region` —— 扫描页的非文字区域（题注靠 OCR 的「图X-Y」token 就近配）。

**不硬凑标签**：配不上题注的图，`caption` 留空、`caption_source="none"`，
照样收进清单。凑一个假题注比没有题注更坏 —— 它会污染下游训练标签。
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

#: 题注：图 / 表 + 编号。编号里的连字符实测有 `-`、`‐`、`⁃`、`.` 多种写法
#: （闾成德书用的是 U+2043 `⁃`），一并收。
CAPTION_RE = re.compile(
    r"^\s*(?P<kind>图|表)\s*(?P<no>[0-9]+(?:[\-‐‑‒–—⁃.][0-9]+)*)"
    r"[\s　:：]*(?P<title>.*)$"
)

#: 行内公式图的尺寸上限（像素）。EPUB 里 `3/2` 这类分式是当图片插的，
#: 实测最小到 29×32 —— 它们不是插图，收进来会淹没真正的图例。
MIN_FIGURE_PX = 60

#: 面积下限。细长的装饰线（如 800×6 的分隔条）宽边达标但不是图。
MIN_FIGURE_AREA_PX = 60 * 60

#: PDF 内嵌图与题注行的最大垂距（点）。中文书题注在图**下方**，
#: 少数在上方，故上下都找，但下方优先。
CAPTION_MAX_DISTANCE_PT = 90.0


@dataclass(frozen=True)
class Figure:
    """一张图（或表）及其题注。"""

    source_key: str
    fig_id: str                      # 全局稳定 id：<source_key>/<局部标识>
    page_index: int                  # 0-based 页/节
    kind: str                        # figure / table / unknown
    caption: str = ""                # 题注全文
    caption_no: str = ""             # "1-15"
    caption_source: str = "none"     # epub_grap / pdf_nearby / page_region / none
    caption_distance_pt: float | None = None
    width: int = 0
    height: int = 0
    origin: str = ""                 # epub_img / pdf_embedded / page_region
    ref: str = ""                    # zip 内路径 / xref 号 / 裁切框
    bbox_pt: tuple[float, float, float, float] | None = None
    context: str = ""                # 图所在段落（供人工核对与检索）
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "source_key": self.source_key, "fig_id": self.fig_id,
            "page_index": self.page_index, "kind": self.kind,
            "caption": self.caption, "caption_no": self.caption_no,
            "caption_source": self.caption_source,
            "caption_distance_pt": self.caption_distance_pt,
            "width": self.width, "height": self.height,
            "origin": self.origin, "ref": self.ref,
            "bbox_pt": list(self.bbox_pt) if self.bbox_pt else None,
            "context": self.context[:400],
        }
        d.update(self.extras)
        return d


def parse_caption(text: str) -> tuple[str, str, str] | None:
    """题注行 → (kind, 编号, 标题)。不是题注返回 None。"""
    m = CAPTION_RE.match((text or "").strip())
    if not m:
        return None
    kind = "table" if m.group("kind") == "表" else "figure"
    return kind, m.group("no"), m.group("title").strip()


def _short_id(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


# --------------------------------------------------------------------------
# 来路一：EPUB
# --------------------------------------------------------------------------

def epub_figures(source) -> list[Figure]:
    """EPUB：`div.pic` 内的题注与图配对，容器内配对**无歧义**。"""
    from bs4 import BeautifulSoup
    from PIL import Image

    from core.knowledge.text_extract import _epub_spine

    out: list[Figure] = []
    with zipfile.ZipFile(source.path) as zf:
        names = set(zf.namelist())
        for page_index, html_name in enumerate(_epub_spine(zf)):
            soup = BeautifulSoup(zf.read(html_name), "html.parser")
            base = html_name.rsplit("/", 1)[0] if "/" in html_name else ""
            for img in soup.find_all("img"):
                src = img.get("src") or ""
                target = _resolve(base, src, names)
                if not target:
                    continue
                try:
                    with Image.open(io.BytesIO(zf.read(target))) as im:
                        w, h = im.size
                except Exception:                   # noqa: BLE001
                    continue
                if min(w, h) < MIN_FIGURE_PX or w * h < MIN_FIGURE_AREA_PX:
                    continue                        # 行内公式，不是插图

                caption, kind, no, csrc = _epub_caption(img)
                out.append(Figure(
                    source_key=source.key,
                    fig_id=f"{source.key}/{Path(target).stem}",
                    page_index=page_index, kind=kind, caption=caption,
                    caption_no=no, caption_source=csrc,
                    width=w, height=h, origin="epub_img", ref=target,
                    context=_nearby_text(img),
                ))
    return out


def _resolve(base: str, src: str, names: set[str]) -> str | None:
    """EPUB 内相对路径 → zip 条目名。"""
    parts = [p for p in (base + "/" + src).split("/") if p not in ("", ".")]
    stack: list[str] = []
    for p in parts:
        if p == "..":
            if stack:
                stack.pop()
        else:
            stack.append(p)
    cand = "/".join(stack)
    if cand in names:
        return cand
    tail = src.rsplit("/", 1)[-1]
    return next((n for n in names if n.endswith("/" + tail)), None)


def _epub_caption(img) -> tuple[str, str, str, str]:
    """在 `div.pic` 容器内找题注。找不到就留空 —— 不向外扩散去猜。"""
    container = img.find_parent(class_="pic") or img.parent
    if container is not None:
        for tag in container.find_all(["p", "div"]):
            parsed = parse_caption(tag.get_text(" ", strip=True))
            if parsed:
                kind, no, title = parsed
                return (tag.get_text(" ", strip=True), kind, no, "epub_grap")
    return "", "unknown", "", "none"


def _nearby_text(img) -> str:
    parent = img.find_parent(class_="pic") or img.parent
    node = parent if parent is not None else img
    prev = node.find_previous_sibling("p")
    return (prev.get_text(" ", strip=True) if prev else "")


# --------------------------------------------------------------------------
# 来路二：PDF 内嵌图（有文本层的书）
# --------------------------------------------------------------------------

def pdf_embedded_figures(source) -> list[Figure]:
    """PDF 内嵌图 + 页面上最近的题注行。有歧义，故记 `caption_distance_pt`。"""
    import fitz

    out: list[Figure] = []
    doc = fitz.open(source.path)
    try:
        for page_index, page in enumerate(doc):
            captions = _page_captions(page)
            for info in page.get_image_info(xrefs=True):
                bbox = info.get("bbox")
                w, h = int(info.get("width", 0)), int(info.get("height", 0))
                if min(w, h) < MIN_FIGURE_PX or w * h < MIN_FIGURE_AREA_PX:
                    continue
                caption, kind, no, dist = _closest_caption(bbox, captions)
                out.append(Figure(
                    source_key=source.key,
                    fig_id=f"{source.key}/p{page_index:04d}x{info.get('xref')}",
                    page_index=page_index, kind=kind, caption=caption,
                    caption_no=no,
                    caption_source="pdf_nearby" if caption else "none",
                    caption_distance_pt=dist, width=w, height=h,
                    origin="pdf_embedded", ref=str(info.get("xref")),
                    bbox_pt=tuple(round(v, 1) for v in bbox) if bbox else None,
                ))
    finally:
        doc.close()
    return out


def _page_captions(page) -> list[tuple[tuple, str, str, str]]:
    """页面上所有题注行 → (bbox, 全文, kind, 编号)。"""
    out = []
    for block in page.get_text("dict").get("blocks", ()):
        for line in block.get("lines", ()):
            text = "".join(s.get("text", "") for s in line.get("spans", ())).strip()
            parsed = parse_caption(text)
            if parsed:
                out.append((tuple(line.get("bbox", ())), text,
                            parsed[0], parsed[1]))
    return out


def _closest_caption(bbox, captions):
    """就近配题注。**下方优先**（中文书题注在图下），同距时取下方。"""
    if not bbox or not captions:
        return "", "unknown", "", None
    x0, y0, x1, y1 = bbox
    cx = (x0 + x1) / 2
    best = None
    for cbox, text, kind, no in captions:
        if not cbox:
            continue
        ccx = (cbox[0] + cbox[2]) / 2
        if abs(ccx - cx) > (x1 - x0) / 2 + 120:
            continue                                # 横向不重叠，不是这张图的题注
        below = cbox[1] - y1
        above = y0 - cbox[3]
        dist = below if below >= 0 else (above if above >= 0 else 0.0)
        penalty = 0.0 if below >= 0 else 25.0       # 上方题注加罚，下方优先
        score = dist + penalty
        if dist <= CAPTION_MAX_DISTANCE_PT and (best is None or score < best[0]):
            best = (score, text, kind, no, dist)
    if best is None:
        return "", "unknown", "", None
    return best[1], best[2], best[3], round(best[4], 1)


def extract(source) -> list[Figure]:
    """按来路分派。扫描件走 `page_region`（见 `scanned_figures.py`），
    此处只处理已有结构的两类。"""
    if source.extract_method == "epub":
        return epub_figures(source)
    if source.extract_method == "text_layer":
        return pdf_embedded_figures(source)
    return []
