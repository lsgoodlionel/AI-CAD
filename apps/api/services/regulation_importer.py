"""
规范知识库导入服务

NLP 提取流水线：
  PDF/Word 文件 → pymupdf4llm Markdown → 段落分割
    → Haiku 批量分类（regulation_classifier）
    → Sonnet 深度提取（regulation_extractor）
    → PostgreSQL regulation_articles 入库
    → Apache AGE 图节点写入（Cypher）
    → Chroma 向量化（双写备用检索）
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# ── 段落分类提示词 ────────────────────────────────────────────

_CLASSIFY_SYSTEM = """\
你是建筑规范条文分类专家。
请对给定的条文段落列表进行分类，每段返回一个类型标签。
输出纯 JSON，格式如下（保持与输入段落顺序一致）：
{"results": [{"index": 0, "type": "simple_rule", "is_mandatory": false}, ...]}
类型值：simple_rule | conditional_rule | cross_ref | definition | other
is_mandatory: 包含"强制"、"必须"、"严禁"等强制义务词时为 true。
不要输出 JSON 以外任何内容。\
"""

_EXTRACT_SYSTEM = """\
你是建筑规范结构化提取专家。
对给定的单条条文，提取结构化信息，输出纯 JSON：
{
  "article_no": "条文编号（如 4.2.3，无则返回 null）",
  "title": "条文小标题（无则返回 null）",
  "obligation_level": "MUST|SHOULD|MAY|MUST_NOT",
  "is_mandatory": true|false,
  "conditions": [{"trigger": "条件描述", "requirement": "要求内容"}],
  "key_params": {"参数名": "参数值"}
}
obligation_level 判断：含"必须/严禁/不应/不得"→ MUST/MUST_NOT；含"应/宜"→ SHOULD；其余→ MAY。
不要输出 JSON 以外任何内容。\
"""


# ── 文本提取 ──────────────────────────────────────────────────

def extract_with_docling(file_bytes: bytes, filename: str = "") -> str | None:
    """docling（MIT）候选前段（D-17）：包一层避免 `services` 直接依赖懒加载细节。

    docling 未安装 / 转换失败时返回 ``None``，调用方无声落回原有降级链，不
    改变默认行为。真实效果由 `scripts/regulation/parse_ab_eval.py` 的离线
    A/B 评测判定，本函数只负责「有则用、无则退」。
    """
    from core.regulation.docling_extract import extract_with_docling as _extract

    return _extract(file_bytes, filename or "regulation.pdf")


#: 每页文本字数低于此值即判为**扫描件**（正文是图像，没有文本层）。
#: 实测住建部强制性通用规范 GB55008-2021：26 页共 1246 字 = **48 字/页**，
#: 且 100% 是「住房城乡建设部信息公开 浏览专用」水印，正文 0 条。
#: 正常排版的规范每页数百到两千字，取 200 作界，离两边都远。
MIN_TEXT_CHARS_PER_PAGE = 200

#: 规范扫描页的 OCR 渲染 dpi。实测 200 已达置信 0.997，
#: 300 反而顺序错乱且慢 4 倍 —— 规范正文是印刷体、版面规整。
REGULATION_OCR_DPI = 200

#: 官方 PDF 的水印行。**只剥已知水印，不做模糊匹配** —— 误删正文的代价
#: 远大于漏掉一行水印。
_WATERMARK_LINES = ("住房城乡建设部信息公开", "浏览专用", "住房和城乡建设部信息公开")


def is_scanned_pdf(text_chars: int, page_count: int) -> bool:
    """文本层产出过少 → 判为扫描件（应转 OCR）。

    **必须按页算**：一本 500 页的规范总字数再多，每页只有水印仍是扫描件。
    页数为 0 不判扫描件 —— 那是解析失败，不该触发 OCR 重试。
    """
    if page_count <= 0:
        return False
    return text_chars / page_count < MIN_TEXT_CHARS_PER_PAGE


def strip_watermark(text: str | None) -> str:
    """剥掉官方 PDF 的水印行（逐行精确匹配）。"""
    if not text:
        return ""
    kept = [
        line for line in str(text).splitlines()
        if not any(mark in line for mark in _WATERMARK_LINES)
    ]
    return "\n".join(kept)


def extract_text_from_pdf(file_bytes: bytes, filename: str = "") -> str:
    """PDF → Markdown 文本。

    优先级链（D-17 新增 docling 前段）：
      docling（若已安装，MIT，版面分析/表格保真更好）
      → pymupdf4llm → pymupdf（原逐级降级，全程保留）。
    docling 缺失或转换失败时 `extract_with_docling` 返回 None，无声落回
    pymupdf4llm→pymupdf 原路径——不改变既有默认行为。
    """
    docling_text = extract_with_docling(file_bytes, filename)
    if docling_text:
        return strip_watermark(docling_text)

    text, page_count = _extract_text_layer(file_bytes)
    # **扫描件转 OCR**：实测住建部强制性通用规范全是扫描件 ——
    # 每页 48 字且 100% 是水印，正文 0 条。文本层链（docling/pymupdf4llm/
    # pymupdf）对它们全部落空，不转 OCR 就等于把水印当正文交出去。
    if is_scanned_pdf(len(text), page_count):
        try:
            recognized = ocr_pdf_text(file_bytes)
        except Exception as exc:  # noqa: BLE001 — OCR 失败不阻断导入
            logger.warning("规范 OCR 失败，退回文本层: %s", exc)
        else:
            if recognized and len(recognized) > len(text):
                return strip_watermark(recognized)
    return strip_watermark(text)


def _extract_text_layer(file_bytes: bytes) -> tuple[str, int]:
    """PDF 文本层 → (文本, 页数)。页数用于判断是否扫描件。"""
    try:
        import fitz

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page_count = doc.page_count
    except Exception as exc:
        raise RuntimeError(f"PDF 解析失败：{exc}") from exc

    try:
        import pymupdf4llm  # type: ignore

        return pymupdf4llm.to_markdown(doc), page_count
    except ImportError:
        pass
    return "\n\n".join(page.get_text() for page in doc), page_count


def ocr_pdf_text(file_bytes: bytes, dpi: int = REGULATION_OCR_DPI) -> str:
    """扫描件规范 → OCR 全文（逐页，按阅读顺序拼接）。

    **dpi 取 200 而非更高**：实测 GB55008 扫描页在 200 dpi 下
    31 token、均置信 **0.997**、3 秒；300 dpi 反而顺序错乱且慢 4 倍。
    规范正文是印刷体、版面规整，200 已足够。
    """
    import fitz
    import numpy as np
    from PIL import Image

    from core.model3d.ocr.service import _recognize_tiled, _select_backend

    backend = _select_backend([])
    if backend is None or not backend.is_available():
        raise RuntimeError("OCR 后端不可用")

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages: list[str] = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        image = Image.fromarray(
            np.frombuffer(pix.samples, dtype=np.uint8)
            .reshape(pix.height, pix.width, pix.n)[:, :, :3])
        tokens = _recognize_tiled(backend, image, [])
        # 按阅读顺序：先上后下、同行左到右
        ordered = sorted(tokens, key=lambda t: (round(t[1][1] / 10), t[1][0]))
        pages.append("\n".join(text for text, _bbox, _conf in ordered if text))
    return "\n\n".join(pages)


def extract_text_from_word(file_bytes: bytes) -> str:
    """Word docx → 纯文本。"""
    try:
        import io
        from docx import Document  # type: ignore
        doc = Document(io.BytesIO(file_bytes))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError as exc:
        raise RuntimeError("python-docx 未安装，无法解析 Word 文件") from exc


def extract_text(file_bytes: bytes, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        return extract_text_from_pdf(file_bytes, filename)
    if ext in ("docx", "doc"):
        return extract_text_from_word(file_bytes)
    raise ValueError(f"不支持的文件格式：{ext}")


# ── 段落分割 ──────────────────────────────────────────────────

_ARTICLE_PATTERN = re.compile(
    r"(?:^|\n)(\d+(?:\.\d+){0,3})\s+(.{0,100})\n([\s\S]*?)(?=\n\d+(?:\.\d+){0,3}\s|\Z)",
    re.MULTILINE,
)
_ARTICLE_LINE_PATTERN = re.compile(r"^\s*\d+(?:\.\d+){1,4}\s+")

_STD_NO_PATTERN = re.compile(
    # 编号部分**只收数字**：原来的 `[\dA-Z./-]+` 在忽略大小写下会把
    # `.pdf` 一起吞掉（实测 `《…》GB55015-2021.pdf` 抽出
    # `GB55015-2021.pdf`），于是说明里的 `GB 55015-2021` 与库里对不上，
    # 「哪些规范还没入库」这张清单就是错的。
    r"\b((?:GB/T|GB/Z|GB|JGJ/T|JGJ|CJJ/T|CJJ|CECS|DBJ|T/CECS)"
    r"\s*\d{1,6}(?:\.\d+)?(?:\s*[-—]\s*\d{4})?)(?![\dA-Za-z])",
    re.IGNORECASE,
)

_DATE_PATTERN = re.compile(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})")


def _parse_date(value: str | date | None) -> date | None:
    if not value or isinstance(value, date):
        return value
    return date.fromisoformat(value)


def infer_book_metadata(text: str, filename: str = "") -> dict[str, Any]:
    """Infer regulation book fields from PDF text and filename."""
    head = "\n".join(line.strip() for line in text.splitlines()[:80] if line.strip())
    source = f"{head}\n{filename}"

    std_match = _STD_NO_PATTERN.search(source)
    std_no = re.sub(r"\s+", "", std_match.group(1)) if std_match else None

    title = None
    for line in head.splitlines()[:30]:
        clean = re.sub(r"\s+", "", line)
        if not clean or len(clean) < 4:
            continue
        if std_no and std_no.replace(" ", "") in clean:
            continue
        if any(token in clean for token in ("规范", "标准", "规程", "规定")):
            title = line.strip()
            break
    if not title:
        title = re.sub(r"\.[Pp][Dd][Ff]$", "", filename).strip() or "未命名规范"

    version_match = re.search(r"(\d{4}\s*年版|\d{4}\s*版|第[一二三四五六七八九十]+版)", source)
    date_match = _DATE_PATTERN.search(source)
    effective_at = None
    if date_match:
        y, m, d = date_match.groups()
        effective_at = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"

    publisher = None
    for line in head.splitlines()[:80]:
        if any(token in line for token in ("住房和城乡建设部", "国家市场监督管理总局", "国家质量监督检验检疫总局")):
            publisher = line.strip()
            break

    discipline = "general"
    discipline_keywords = [
        ("消防", "fire"),
        ("防火", "fire"),
        ("混凝土", "structure"),
        ("钢结构", "structure"),
        ("结构", "structure"),
        ("建筑", "architecture"),
        ("给水", "mep"),
        ("排水", "mep"),
        ("暖通", "mep"),
        ("电气", "mep"),
        ("装修", "decoration"),
        ("装饰", "decoration"),
    ]
    for keyword, value in discipline_keywords:
        if keyword in source:
            discipline = value
            break

    return {
        "title": title[:300],
        "std_no": std_no[:100] if std_no else None,
        "version": version_match.group(1).replace(" ", "")[:50] if version_match else None,
        "discipline": discipline,
        "publisher": publisher[:200] if publisher else None,
        "effective_at": effective_at,
    }


#: 义务词（GB/T 1.1 标准编写规则）——规范条文的核心标志。
_OBLIGATION_WORDS = ("必须", "严禁", "不应", "不得", "不宜", "应", "宜", "可")

#: 目录行的指纹：省略号引导页码，或整行只有短标题。
_TOC_DOTS_RE = re.compile(r"[·.．]{3,}|[·.．]{2,}\s*\d+\s*$")

#: 条文正文的最短长度（含义务词时可放宽）。
MIN_ARTICLE_BODY_CHARS = 12


#: 工程建设**强制性国家标准**（GB 55001~55037「通用规范」系列）。
#: 住建部规定其**全部条文必须严格执行** —— 强制性由**规范类型**决定，
#: 而义务词（应/不应/宜）区分的是**要求的严格程度**，两者是不同维度。
#: 实测防水通用规范 159 条只有 72 条被标强条，正是把两者混为一谈。
_MANDATORY_STD_NO_RE = re.compile(r"GB\s?550\d{2}")
_MANDATORY_NAME_RE = re.compile(r"通用规范")


def is_mandatory_standard(title: str | None) -> bool:
    """该规范是否**全文强制**（GB 55xxx 通用规范系列）。

    编号或名称任一命中即可 —— 实测文件名格式不统一。
    **判不出就不标强条**：误标会让审图误报。
    """
    text = str(title or "")
    if not text.strip():
        return False
    return bool(_MANDATORY_STD_NO_RE.search(text.replace("　", " "))
                or _MANDATORY_NAME_RE.search(text))


def looks_like_article(text: str | None) -> bool:
    """这段文字像**规范条文**吗（而非目录/页码/标题）。

    判据（GB/T 1.1）：含**义务词**，或是**完整句子**（有句号且够长）。
    纯页码、短标题一律不算 —— 实测 `11`、`12` 曾被当成条文号入库。
    """
    body = str(text or "").strip()
    if len(body) < 6 or body.isdigit():
        return False
    stripped = re.sub(r"^[\d.．\s]+", "", body)
    if len(stripped) < 4:
        return False                       # 去掉编号后没剩什么 —— 是页码或序号
    if any(word in stripped for word in _OBLIGATION_WORDS):
        return True
    return len(stripped) >= MIN_ARTICLE_BODY_CHARS and (
        "。" in stripped or "；" in stripped)


def is_toc_line(text: str | None) -> bool:
    """这行是**目录**吗。

    目录行是「短标题 + 页码」，没有义务词也不成句；
    正文则相反。实测规范前几页全是目录，
    而 `4.3 构造要求`、`5.1 个人防护` 形似条文号，会被切分器当成条文。
    """
    body = str(text or "").strip()
    if not body:
        return False
    if _TOC_DOTS_RE.search(body):
        return True                        # 省略号引导页码 —— 确凿的目录
    return not looks_like_article(body)


#: 三级条文号（GB/T 1.1）：`2.0.3` / `4.1.2`。**紧贴正文无空格**是
#: OCR 文本的常态（`2.0.3混凝土结构…`），所以不能按空格切。
_OCR_ARTICLE_NO_RE = re.compile(r"^(\d{1,2}\.\d{1,2}\.\d{1,2})\s*(.*)$")

#: 页码行的上限位数 —— 规范不会有四位页码。
MAX_PAGE_NUMBER_DIGITS = 3


def is_page_number_line(line: str | None) -> bool:
    """这行是**孤立的页码**吗。

    实测 OCR 把页码单独成行，且**插在句子中间**：

        2.0.4…强度设计值取值应符合
        2                      ← 页码
        下列规定：

    不剔除的话，一条会被腰斩成两半。
    """
    body = str(line or "").strip()
    return bool(body) and body.isdigit() and len(body) <= MAX_PAGE_NUMBER_DIGITS


def split_ocr_articles(text: str | None) -> list[dict[str, str]]:
    """OCR 全文 → 条文列表（按三级条文号切分）。

    **为什么单独写**：`split_into_paragraphs` 的判据为**有排版结构的
    文本层**设计，对 OCR 出的连续文本失效 —— 实测入库的前几条全是
    目录碎片，页码还被当成了条文号。

    三个实测形态：
    - 条文号**紧贴正文无空格**（`2.0.3混凝土…`）
    - **页码孤立成行插在句中** —— 剔除后句子才接得上
    - **子项用裸数字编号**（`1结构混凝土…`）—— 属于上一条，不切
    """
    lines = [ln.strip() for ln in str(text or "").splitlines()]
    articles: list[dict[str, str]] = []
    current_no: str | None = None
    buffer: list[str] = []

    def _flush() -> None:
        nonlocal current_no, buffer
        if current_no is not None:
            body = "".join(buffer).strip()
            if looks_like_article(body):
                articles.append({"index": len(articles),
                                 "article_no": current_no,
                                 "text": f"{current_no} {body}".strip()})
        current_no, buffer = None, []

    for line in lines:
        if not line or is_page_number_line(line):
            continue                       # 页码不打断条文
        matched = _OCR_ARTICLE_NO_RE.match(line)
        if matched:
            _flush()
            current_no = matched.group(1)
            buffer = [matched.group(2)]
        elif current_no is not None:
            buffer.append(line)            # 断行接上（OCR 按视觉行输出）
    _flush()
    return articles


def split_into_paragraphs(text: str) -> list[dict[str, str]]:
    """
    尝试按条文编号（4.2.3）分段，若无法识别则按 2 个换行分段。
    返回 [{"index": i, "text": "..."}]
    """
    paras: list[dict[str, str]] = []
    matches = list(_ARTICLE_PATTERN.finditer(text))

    if len(matches) >= 3:
        for m in matches:
            body = f"{m.group(1)} {m.group(2)}\n{m.group(3)}".strip()
            # **目录不是条文**：实测规范前几页全是目录，
            # `4.3 构造要求`、`5.1 个人防护` 形似条文号会被切进来。
            if len(body) > 20 and looks_like_article(body):
                paras.append({"index": len(paras), "text": body})
    else:
        current: list[str] = []
        for line in (line.strip() for line in text.splitlines()):
            if not line:
                continue
            if _ARTICLE_LINE_PATTERN.match(line):
                if current and len("\n".join(current)) > 20:
                    paras.append({"index": len(paras), "text": "\n".join(current)})
                current = [line]
            elif current:
                current.append(line)
        if current and len("\n".join(current)) > 20:
            paras.append({"index": len(paras), "text": "\n".join(current)})

        if not paras:
            chunks = [c.strip() for c in re.split(r"\n{2,}", text) if len(c.strip()) > 30]
            for chunk in chunks:
                paras.append({"index": len(paras), "text": chunk})

    return paras


def local_classify_paragraph(paragraph: dict[str, str]) -> dict[str, Any]:
    text = paragraph["text"]
    is_mandatory = any(word in text for word in ("必须", "严禁", "不得", "不应"))
    has_rule_word = any(word in text for word in ("应", "宜", "可", "必须", "不得", "严禁", "不应"))
    has_article_no = re.match(r"^\s*\d+(?:\.\d+){0,4}", text) is not None
    return {
        "index": paragraph["index"],
        "type": "simple_rule" if has_rule_word or has_article_no else "other",
        "is_mandatory": is_mandatory,
    }


def local_extract_article(paragraph: dict[str, str], classify_result: dict[str, Any]) -> dict[str, Any]:
    text = paragraph["text"].strip()
    first_line = text.splitlines()[0] if text else ""
    match = re.match(r"^\s*(\d+(?:\.\d+){0,4})\s*(.*)$", first_line)
    article_no = match.group(1) if match else None
    title = (match.group(2).strip() if match and match.group(2).strip() else None)

    if "严禁" in text or "不得" in text or "不应" in text:
        obligation = "MUST_NOT"
    elif "必须" in text:
        obligation = "MUST"
    elif "应" in text:
        obligation = "SHOULD"
    elif "宜" in text or "可" in text:
        obligation = "MAY"
    else:
        obligation = "SHOULD"

    return {
        "article_no": article_no,
        "title": title[:300] if title else None,
        "obligation_level": obligation,
        "is_mandatory": bool(classify_result.get("is_mandatory")),
        "conditions": [],
        "key_params": {},
        "raw_text": text,
        "article_type": classify_result.get("type", "simple_rule"),
    }


# ── LLM 分类 ─────────────────────────────────────────────────

async def classify_paragraphs(
    paragraphs: list[dict[str, str]],
    router: Any,
    batch_size: int = 20,
) -> list[dict[str, Any]]:
    """
    分批调用 regulation_classifier 引擎对段落分类。
    返回与 paragraphs 对应的分类结果列表。
    """
    results: list[dict[str, Any]] = [{}] * len(paragraphs)

    for start in range(0, len(paragraphs), batch_size):
        batch = paragraphs[start : start + batch_size]
        numbered = "\n\n".join(
            f"[{p['index']}] {p['text'][:500]}" for p in batch
        )
        try:
            resp = await router.route(
                "regulation_classifier",
                [
                    {"role": "system", "content": _CLASSIFY_SYSTEM},
                    {"role": "user", "content": f"请对以下条文段落分类：\n\n{numbered}"},
                ],
                task_type="batch",
            )
            parsed = json.loads(resp.content)
            for item in parsed.get("results", []):
                idx = item.get("index", -1)
                if 0 <= idx < len(paragraphs):
                    results[idx] = item
        except Exception as exc:
            logger.warning("classify_paragraphs batch %d failed: %s", start, exc)

    if not any(results):
        return [local_classify_paragraph(p) for p in paragraphs]
    return results


# ── LLM 深度提取 ──────────────────────────────────────────────

async def extract_article(
    paragraph: dict[str, str],
    classify_result: dict[str, Any],
    router: Any,
) -> dict[str, Any]:
    """
    对单条段落调用 regulation_extractor 深度提取结构化信息。
    低置信度或提取失败时返回降级数据（is_mandatory 信号来自分类步骤）。
    """
    text = paragraph["text"]
    article_type = classify_result.get("type", "other")
    is_mandatory_hint = classify_result.get("is_mandatory", False)

    if article_type == "other":
        return _fallback_article(text, is_mandatory_hint)

    try:
        resp = await router.route(
            "regulation_extractor",
            [
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": f"请提取以下条文的结构化信息：\n\n{text[:2000]}"},
            ],
            task_type="primary",
        )
        parsed = json.loads(resp.content)
        parsed.setdefault("article_type", article_type)
        parsed.setdefault("is_mandatory", is_mandatory_hint)
        parsed["raw_text"] = text
        return parsed
    except Exception as exc:
        logger.warning("extract_article failed: %s", exc)
        return local_extract_article(paragraph, classify_result)


def _fallback_article(text: str, is_mandatory: bool) -> dict[str, Any]:
    """提取失败时的降级结构（保留原文，供人工审核）。"""
    no_match = re.match(r"^(\d+(?:\.\d+){0,3})\s", text)
    return {
        "article_no": no_match.group(1) if no_match else None,
        "title": None,
        "obligation_level": "MUST" if is_mandatory else "SHOULD",
        "is_mandatory": is_mandatory,
        "conditions": [],
        "key_params": {},
        "raw_text": text,
        "article_type": "other",
    }


# ── 数据库写入 ────────────────────────────────────────────────

async def save_articles_to_db(
    db: Any,
    book_id: str,
    articles: list[dict[str, Any]],
    book_title: str | None = None,
) -> list[str]:
    """
    批量写入 regulation_articles 表，已存在（book_id + article_no）则跳过。
    返回写入的 article_id 列表。
    """
    saved_ids: list[str] = []

    for art in articles:
        params = build_article_params(book_id, art, book_title)
        if params is None:
            continue
        article_no = params["article_no"]
        try:
            existing = await db.fetch_val(
                "SELECT id FROM regulation_articles "
                "WHERE book_id = CAST(:book_id AS uuid) AND article_no = :article_no",
                {"book_id": book_id, "article_no": article_no},
            )
            if existing:
                saved_ids.append(str(existing))
                continue

            row = await db.fetch_one(
                """
                INSERT INTO regulation_articles
                    (book_id, article_no, title, content, obligation_level,
                     is_mandatory, conditions)
                VALUES (CAST(:book_id AS uuid), :article_no, :title, :content,
                        :obligation_level, :is_mandatory,
                        CAST(:conditions AS jsonb))
                RETURNING id
                """,
                params,
            )
            saved_ids.append(str(row["id"]))
        except Exception as exc:  # noqa: BLE001 — 单条失败不中断整本
            logger.error("save_article %s failed: %s", article_no, exc)

    return saved_ids


#: 已经表达强制的义务等级——抬升时保持原样，不把「严禁」弱化成「必须」。
_MANDATORY_LEVELS = ("MUST", "MUST_NOT")


def build_article_params(book_id: str, article: dict[str, Any],
                         book_title: str | None = None) -> dict | None:
    """条文 → 入库参数（**databases 风格的字典**）；空正文返回 None。

    **为什么单独抽出来**：此前这段用 **asyncpg 风格**（`$1` 占位 + 位置参数），
    而本项目用 databases + SQLAlchemy（`:name` + 字典）—— 每条都报
    `bindparams() argument after ** must be a mapping`，
    而错误被 except 吞成 logger.error，导入仍报「成功」。
    **这条路径从未被真正执行过**（规范库一直是空的），所以没人发现。
    """
    content = (article or {}).get("raw_text") or ""
    if not content:
        return None
    # **通用规范全文强制**（见 `is_mandatory_standard`）：
    # 强制性由规范类型决定，不能只看义务词。
    mandatory = (bool(article.get("is_mandatory", False))
                 or is_mandatory_standard(book_title))
    level = article.get("obligation_level") or "SHOULD"
    if mandatory and level not in _MANDATORY_LEVELS:
        # 两个字段不能自相矛盾：强制为真却写 SHOULD，下游按不同字段
        # 会得出不同结论（审图报告看等级、图谱按 mandatory 过滤）。
        # `MUST_NOT`（严禁）比 `MUST` 更强，抬升时不得反被弱化。
        level = "MUST"
    return {
        "book_id": book_id,
        "article_no": article.get("article_no") or f"AUTO-{uuid.uuid4().hex[:8]}",
        "title": article.get("title"),
        "content": content,
        "obligation_level": level,
        "is_mandatory": mandatory,
        "conditions": json.dumps(article.get("conditions", []),
                                 ensure_ascii=False),
    }


# ── AGE 图节点写入 ────────────────────────────────────────────

def build_article_query(article_id: str) -> tuple[str, dict]:
    """取条文用于建图 → (SQL, 参数字典)。

    **为什么单独抽出来**：此前这里用 **asyncpg 风格**（`$1` + 位置参数），
    与 `save_articles_to_db` 同源缺陷 —— 实测每条都报
    `TextClause.bindparams() argument after ** must be a mapping`。

    后果比条文入库更隐蔽：**KG 引擎（四引擎之一）依赖 AGE 里的 Article
    节点做条件合规推理**，节点没写进去就等于该引擎空转，
    而错误被 except 吞成 logger.error，导入照常报成功。
    """
    return (
        "SELECT id, article_no, content, obligation_level, is_mandatory "
        "FROM regulation_articles WHERE id = CAST(:article_id AS uuid)",
        {"article_id": article_id},
    )


#: 可写入 regulation_books 的元数据字段（白名单，防 SQL 注入）。
_BOOK_META_FIELDS = ("title", "std_no", "version", "discipline",
                     "publisher", "effective_at")


def build_book_update(book_id: str, fields: dict) -> tuple[str, dict] | None:
    """书元数据更新 → (SQL, 参数字典)；无字段可更新返回 None。

    **同源缺陷第三处**：此前用 `$1` + 位置参数，导致
    **标准号、版本、生效日期全都没写进去** —— 而版本比对
    （判断图纸引用的规范是否已失效）正依赖这些字段。
    """
    usable = {key: value for key, value in (fields or {}).items()
              if key in _BOOK_META_FIELDS}
    if not usable:
        return None
    sets = ", ".join(f"{key} = :{key}" for key in usable)
    params = dict(usable)
    params["book_id"] = book_id
    return (f"UPDATE regulation_books SET {sets}, updated_at = now() "
            f"WHERE id = CAST(:book_id AS uuid)", params)


#: KG 图名——**必须与 kg_engine.KG_GRAPH_NAME 一致**。
#: 此前写入端用 `cad_graph`、读取端用 `regulation_graph`，两边永不相交。
GRAPH_NAME = "regulation_graph"

#: `build_clause_statements` 里条文节点语句的下标（第 0 条是 Standard）。
CLAUSE_STATEMENT_INDEX = 1


def cypher_literal(value: object) -> str:
    """转义成 cypher 单引号字面量。

    规范标题含书名号、条文含引号是常态；不转义会把语句打断，
    甚至让条文内容里的 `RETURN` 变成可执行片段。

    **注意用的是 cypher 的转义规约（反斜杠）而非 SQL 的（双写单引号）**：
    语句整体包在 `$$ … $$` 里，双写单引号会原样传进 cypher 解析器并报
    `syntax error at or near "'C25'"` —— 单测只比字符串形状，抓不到这个，
    是对真库的端到端探测暴露的。
    """
    return (str(value if value is not None else "")
            .replace("\\", "\\\\")
            .replace("'", "\\'"))


#: 标题里出现即判为正文句子的标点——正文句子最容易被误抽成标题。
_TITLE_PROSE_MARKS = "，。；、！？：,;!?"

#: 规范标题的长度上限（含标准号前缀时也够用）。
MAX_BOOK_TITLE_CHARS = 60


def is_plausible_book_title(candidate: str | None) -> bool:
    """判断一个候选串是否像规范标题。

    实测有书名被抽成序言里的半句话（「为适应国际技术法规……2016年以来，」）。
    后果直指需求：规范库要能被**总说明里的引用检索到**、要能**跨版本比对**，
    一本叫做半句话的书两样都做不到。
    """
    text = (candidate or "").strip()
    if not text or len(text) > MAX_BOOK_TITLE_CHARS:
        return False
    return not any(mark in text for mark in _TITLE_PROSE_MARKS)


def strip_file_extension(name: str) -> str:
    """去掉路径与扩展名，得到文件名标题。"""
    base = (name or "").rsplit("/", 1)[-1]
    for suffix in (".pdf", ".docx", ".doc", ".txt"):
        if base.lower().endswith(suffix):
            return base[: -len(suffix)]
    return base


def resolve_book_title(filename_title: str, extracted: str | None) -> str:
    """定标题：文件名里的《…》优先，其次是合格的抽取结果。

    人工上传的文件名是**人写的**，比从正文里猜可靠。
    抽取结果不合格时保留文件名——宁可粗糙，不写脏值。
    """
    if "《" in (filename_title or "") and "》" in (filename_title or ""):
        return filename_title
    return extracted if is_plausible_book_title(extracted) else filename_title


#: 标准号前缀 → 归一后带一个空格。总说明写 `GB 55023-2022`，
#: 抽取常得到 `GB55023-2022`，不归一化引用就永远匹配不上。
_STD_NO_RE = re.compile(
    r"^\s*(GB/T|GB/Z|GB|JGJ/T|JGJ|CJJ/T|CJJ|JTG|DL/T|DL|TB|CECS|T/[A-Z]+)"
    r"\s*(\d[\d.\-]*)\s*$", re.IGNORECASE)


def normalize_std_no(raw: str | None) -> str | None:
    """标准号归一为「前缀 + 单空格 + 编号」；认不出的形状原样保留。"""
    if raw is None:
        return None
    match = _STD_NO_RE.match(raw)
    if not match:
        return raw
    return f"{match.group(1).upper()} {match.group(2)}"


def coerce_age_node_id(value: object) -> int | None:
    """agtype 节点 id → int；转不动返回 None（不写脏值）。

    `age_node_id` 列是 bigint，而 agtype 回来的是带引号的字符串，
    直接写会报 `'str' object cannot be interpreted as an integer` ——
    图建对了、条文与节点的关联却全丢。
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip().strip('"'))
    except (TypeError, ValueError):
        return None


def build_clause_statements(book: dict, article: dict) -> list[str]:
    """条文 → AGE 语句**列表**，建的是读取端 MATCH 的那套标签。

    读取端查 `(d:Discipline)-[:REQUIRES]->(s:Standard)-[:HAS_CLAUSE]->(c:Clause)`，
    而此前写入端只建扁平的 `(a:Article)`，且写进另一张图 `cad_graph` ——
    图名和标签双双对不上，等于 KG 引擎的图谱推理从未生效。

    **为什么必须拆成多条**——实测 AGE 1.5.0 的行为：
    一条语句里**只有第一个 MERGE 绑定的变量能被 SET**，其余节点的
    属性赋值被**静默丢弃**（不报错、不警告）。把赋值项换序也没用，
    决定权在 MERGE 顺序而非赋值顺序。同一节点的多个属性则正常。
    这条只有对真库实跑才暴露：语句字符串完全合法，单测比不出来。

    **关系必须具名** `-[h:HAS_CLAUSE]->`：SQLAlchemy 的绑定参数正则
    要求 `:` 前不是词字符，匿名的 `-[:HAS_CLAUSE]->` 会被当成参数
    `:HAS_CLAUSE` 编译成 `$1`，AGE 报 `unexpected character at or near "$"`。
    用 asyncpg 直连时不暴露（不解析 `:`），本项目两条路径都有。

    没有专业字段时**不编造 Discipline 节点**：宁可这条走不到专业入口，
    也不能凭空造出一个错的专业归属。
    """
    std_code = cypher_literal(book.get("std_no") or book.get("id"))
    clause_id = cypher_literal(article.get("id"))
    mandatory = "true" if article.get("is_mandatory") else "false"

    def wrap(body: str) -> str:
        # **限定 ag_catalog**：`databases` 走连接池，`SET search_path`
        # 设在一条连接上，后续语句落到另一条就报
        # `function cypher(unknown, unknown) does not exist`。
        return (f"SELECT * FROM ag_catalog.cypher('{GRAPH_NAME}', $$ {body} $$)"
                " AS (result ag_catalog.agtype)")

    statements = [
        wrap(f"MERGE (s:Standard {{code: '{std_code}'}})"
             f" SET s.name = '{cypher_literal(book.get('title'))}' RETURN id(s)"),
        wrap(f"MERGE (c:Clause {{id: '{clause_id}'}})"
             f" SET c.article_no = '{cypher_literal(article.get('article_no'))}',"
             f" c.content = '{cypher_literal(article.get('content'))}',"
             f" c.obligation_level = '{cypher_literal(article.get('obligation_level'))}',"
             f" c.mandatory = {mandatory} RETURN id(c)"),
        wrap(f"MERGE (s:Standard {{code: '{std_code}'}})"
             f" MERGE (c:Clause {{id: '{clause_id}'}})"
             " MERGE (s)-[h:HAS_CLAUSE]->(c) RETURN id(c)"),
    ]
    discipline = book.get("discipline")
    if discipline:
        code = cypher_literal(discipline)
        statements.append(wrap(f"MERGE (d:Discipline {{code: '{code}'}}) RETURN id(d)"))
        statements.append(
            wrap(f"MERGE (d:Discipline {{code: '{code}'}})"
                 f" MERGE (s:Standard {{code: '{std_code}'}})"
                 " MERGE (d)-[q:REQUIRES]->(s) RETURN id(s)"))
    return statements


async def build_age_nodes(
    db: Any,
    book_id: str,
    article_ids: list[str],
) -> None:
    """
    在 Apache AGE 中为每篇条文创建图节点，并与规范文件建立 HAS_ARTICLE 关系。
    AGE 不可用时静默跳过。
    """
    book = await db.fetch_one(
        "SELECT id, title, std_no, discipline FROM regulation_books "
        "WHERE id = CAST(:book_id AS uuid)", {"book_id": book_id})
    if not book:
        logger.warning("建图跳过：规范书 %s 不存在", book_id)
        return
    book_row = dict(book)

    # **必须钉在同一条连接上**：`LOAD 'age'` 是会话级的，
    # 而 `databases` 每条语句都可能换连接。事务把连接固定住。
    try:
        async with db.transaction():
            await db.execute("LOAD 'age'")
            await db.execute("SET search_path = ag_catalog, '$user', public")
            for article_id in article_ids:
                try:
                    sql, params = build_article_query(article_id)
                    art = await db.fetch_one(sql, params)
                    if not art:
                        continue

                    node_id = None
                    statements = build_clause_statements(book_row, dict(art))
                    for index, statement in enumerate(statements):
                        row = await db.fetch_one(statement)
                        # 第 0 条返回 Standard 节点，条文节点是第 1 条 ——
                        # age_node_id 存的是条文，取错就把整本书的条文
                        # 全指到同一个标准节点上。
                        if index == CLAUSE_STATEMENT_INDEX and row is not None:
                            node_id = row[0]
                    node_id = coerce_age_node_id(node_id)
                    if node_id is not None:
                        await db.execute(
                            "UPDATE regulation_articles SET age_node_id = :node_id "
                            "WHERE id = CAST(:article_id AS uuid)",
                            {"node_id": node_id, "article_id": article_id},
                        )
                except Exception as exc:
                    logger.warning("AGE node for article %s failed: %s",
                                   article_id, exc)
    except Exception as exc:
        logger.warning("AGE 图写入整体失败（扩展不可用？）：%s", exc)


# ── Chroma 向量化 ─────────────────────────────────────────────

def build_vectorize_fetch(article_id: str) -> tuple[str, dict]:
    """向量化取数 → (SQL, 参数字典)。

    **第四处同源缺陷**：此前是 `$1` + 位置参数，每条都报
    `TextClause.bindparams() argument after ** must be a mapping`，
    被吞成 warning 后 **RAG 引擎（四引擎之三）的向量库一条都没有**，
    而导入照常报「条文 75/75」成功。
    """
    return (
        "SELECT id, article_no, content, obligation_level, is_mandatory, book_id "
        "FROM regulation_articles WHERE id = CAST(:article_id AS uuid)",
        {"article_id": article_id},
    )


#: Chroma 默认嵌入模型的本地缓存目录。
DEFAULT_EMBEDDING_CACHE = Path.home() / ".cache" / "chroma" / "onnx_models"

#: Chroma 默认嵌入模型名。**注意它是英文模型**——用它给中文规范条文
#: 做向量检索质量有限，而引擎参数里配的是 bge-m3。容器内没有
#: sentence-transformers/transformers，暂时只有这一个零安装选项，
#: 已记入 `docs/PHASE_J_BLUEPRINT.md` 待办。
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

#: 判定模型已解包就绪的最小体积——下到一半的目录比没有更危险。
_MIN_ONNX_BYTES = 512


def embedding_model_ready(
    cache_dir: Path | None = None,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
) -> bool:
    """本地是否已有**解包完成**的嵌入模型。

    **为什么要判**：Chroma 首次调用会现场下载模型（79MB），
    实测容器内 2 分 20 秒只到 6%，整批规范全被这一个下载卡住。
    按项目的设计约束（缺失不得阻断、降级必须可见），
    没就绪就跳过向量化，导入照常完成，向量另行回填。
    """
    root = Path(cache_dir) if cache_dir else DEFAULT_EMBEDDING_CACHE
    model_dir = root / model_name / "onnx"
    if not model_dir.is_dir():
        return False
    onnx = model_dir / "model.onnx"
    return (onnx.is_file() and onnx.stat().st_size >= _MIN_ONNX_BYTES
            and (model_dir / "tokenizer.json").is_file())


def _chroma_collection():
    """连 Chroma 并取集合——**调用它可能触发模型下载**，
    所以务必先过 `embedding_model_ready`。"""
    import chromadb  # type: ignore
    from core.config import settings

    client = chromadb.HttpClient(host=settings.chroma_host,
                                 port=settings.chroma_port)
    return client.get_or_create_collection("regulation_articles")


async def vectorize_articles(
    db: Any,
    article_ids: list[str],
) -> None:
    """
    将条文内容写入 Chroma 向量库，失败时静默跳过（不影响主流程）。
    collection 名称：regulation_articles
    """
    if not embedding_model_ready():
        logger.warning(
            "嵌入模型未就绪（%s），**跳过向量化**——导入不因此阻断，"
            "条文的 vector_id 会留空，可另行回填。",
            DEFAULT_EMBEDDING_CACHE / DEFAULT_EMBEDDING_MODEL)
        return
    try:
        collection = _chroma_collection()
    except Exception as exc:
        logger.warning("Chroma 不可用，跳过向量化：%s", exc)
        return

    docs, ids, metas = [], [], []

    for article_id in article_ids:
        try:
            sql, params = build_vectorize_fetch(article_id)
            art = await db.fetch_one(sql, params)
            if not art:
                continue
            docs.append(art["content"][:2000])
            ids.append(article_id)
            metas.append({
                "article_no": art["article_no"] or "",
                "obligation_level": art["obligation_level"],
                "is_mandatory": str(art["is_mandatory"]),
                "book_id": str(art["book_id"]),
            })
        except Exception as exc:
            logger.warning("fetch article %s for vectorize failed: %s", article_id, exc)

    if not docs:
        return

    try:
        collection.upsert(documents=docs, ids=ids, metadatas=metas)
        for article_id in ids:
            await db.execute(
                "UPDATE regulation_articles SET vector_id = :vector_id "
                "WHERE id = CAST(:article_id AS uuid)",
                {"vector_id": article_id, "article_id": article_id},
            )
        logger.info("vectorized %d articles", len(ids))
    except Exception as exc:
        logger.warning("Chroma upsert failed: %s", exc)


# ── 主入口 ────────────────────────────────────────────────────

async def import_regulation_file(
    db: Any,
    router: Any,
    book_id: str,
    file_bytes: bytes,
    filename: str,
    batch_size: int = 20,
    confidence_min: float = 0.7,
) -> dict[str, Any]:
    """
    完整的规范文件导入流水线。
    返回 {"total": N, "saved": M, "skipped": K, "article_ids": [...]}
    """
    text = extract_text(file_bytes, filename)
    metadata = infer_book_metadata(text, filename)
    # **书名先定下来再往下传**：强条判定、图节点、引用检索全依赖它。
    resolved_title = resolve_book_title(
        strip_file_extension(filename), metadata.get("title"))
    await _update_book_metadata(db, book_id, metadata, filename=filename)

    # **OCR 文本用专用切分**：`split_into_paragraphs` 的判据为有排版结构的
    # 文本层设计，对 OCR 出的连续文本失效（实测入库前几条全是目录碎片，
    # 页码还被当成条文号）。三级条文号切得出就用它，切不出再落回原路径。
    paragraphs = split_ocr_articles(text)
    if len(paragraphs) < 3:
        paragraphs = split_into_paragraphs(text)
    logger.info("book %s: split %d paragraphs from %s", book_id, len(paragraphs), filename)

    classify_results = await classify_paragraphs(paragraphs, router, batch_size)

    # 过滤 other 类型（非条文内容）
    to_extract = [
        (p, c) for p, c in zip(paragraphs, classify_results)
        if c.get("type", "other") != "other"
    ]
    if not to_extract and paragraphs:
        classify_results = [local_classify_paragraph(p) for p in paragraphs]
        to_extract = [
            (p, c) for p, c in zip(paragraphs, classify_results)
            if c.get("type", "other") != "other"
        ]
    logger.info("book %s: %d/%d paragraphs to extract", book_id, len(to_extract), len(paragraphs))

    articles = []
    for para, cls_result in to_extract:
        art = await extract_article(para, cls_result, router)
        articles.append(art)

    # **书名要传下去**：通用规范全文强制，这个判定只能从书名得出
    article_ids = await save_articles_to_db(
        db, book_id, articles, book_title=resolved_title)
    await build_age_nodes(db, book_id, article_ids)
    await vectorize_articles(db, article_ids)

    return {
        "total": len(paragraphs),
        "extracted": len(to_extract),
        "saved": len(article_ids),
        "skipped": len(paragraphs) - len(to_extract),
        "article_ids": article_ids,
        "metadata": metadata,
    }


async def _update_book_metadata(
    db: Any,
    book_id: str,
    metadata: dict[str, Any],
    filename: str = "",
) -> None:
    """把抽取到的元数据写回书行——**标题和标准号先过质量守卫**。

    实测抽取会把序言里的半句话当成书名。规范库要能被总说明的引用
    检索到、要能跨版本比对，所以这两个字段宁可保守也不能写脏。
    """
    fields = {
        key: value
        for key, value in metadata.items()
        if value and key in {"title", "std_no", "version", "discipline", "publisher", "effective_at"}
    }
    filename_title = strip_file_extension(filename)
    resolved = resolve_book_title(filename_title, fields.get("title"))
    if resolved and resolved != filename_title:
        fields["title"] = resolved
    else:
        fields.pop("title", None)      # 与文件名一致就不必回写
    if "std_no" in fields:
        fields["std_no"] = normalize_std_no(fields["std_no"])
    if "effective_at" in fields:
        fields["effective_at"] = _parse_date(fields["effective_at"])
    if not fields:
        return
    built = build_book_update(book_id, fields)
    if built is None:
        return
    sql, params = built
    try:
        await db.execute(sql, params)
    except Exception as exc:
        logger.warning("update regulation book metadata failed: %s", exc)
