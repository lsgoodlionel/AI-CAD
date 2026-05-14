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

def extract_text_from_pdf(file_bytes: bytes) -> str:
    """PDF → Markdown 文本，优先 pymupdf4llm，降级到 pymupdf。"""
    try:
        import pymupdf4llm  # type: ignore
        import fitz
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        return pymupdf4llm.to_markdown(doc)
    except ImportError:
        pass

    try:
        import fitz
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        pages = []
        for page in doc:
            pages.append(page.get_text())
        return "\n\n".join(pages)
    except Exception as exc:
        raise RuntimeError(f"PDF 解析失败：{exc}") from exc


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
        return extract_text_from_pdf(file_bytes)
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
    r"\b((?:GB|GB/T|JGJ|CJJ|CECS|DBJ|T/CECS)\s*[\dA-Z./-]+(?:\s*[-—]\s*\d{4})?)\b",
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
            if len(body) > 20:
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
                    {"role": "user", "content": f"请对以下条文段落分类：\n\n{numbered}"},
                ],
                task_type="batch",
                system=_CLASSIFY_SYSTEM,
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
                {"role": "user", "content": f"请提取以下条文的结构化信息：\n\n{text[:2000]}"},
            ],
            task_type="primary",
            system=_EXTRACT_SYSTEM,
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
) -> list[str]:
    """
    批量写入 regulation_articles 表，已存在（book_id + article_no）则跳过。
    返回写入的 article_id 列表。
    """
    saved_ids: list[str] = []

    for art in articles:
        article_no = art.get("article_no") or f"AUTO-{uuid.uuid4().hex[:8]}"
        content = art.get("raw_text", "")
        if not content:
            continue

        try:
            existing = await db.fetch_val(
                "SELECT id FROM regulation_articles WHERE book_id=$1 AND article_no=$2",
                book_id, article_no,
            )
            if existing:
                saved_ids.append(str(existing))
                continue

            row = await db.fetch_one(
                """
                INSERT INTO regulation_articles
                    (book_id, article_no, title, content, obligation_level,
                     is_mandatory, conditions)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                RETURNING id
                """,
                book_id,
                article_no,
                art.get("title"),
                content,
                art.get("obligation_level", "SHOULD"),
                bool(art.get("is_mandatory", False)),
                json.dumps(art.get("conditions", []), ensure_ascii=False),
            )
            saved_ids.append(str(row["id"]))
        except Exception as exc:
            logger.error("save_article %s failed: %s", article_no, exc)

    return saved_ids


# ── AGE 图节点写入 ────────────────────────────────────────────

async def build_age_nodes(
    db: Any,
    book_id: str,
    article_ids: list[str],
) -> None:
    """
    在 Apache AGE 中为每篇条文创建图节点，并与规范文件建立 HAS_ARTICLE 关系。
    AGE 不可用时静默跳过。
    """
    try:
        await db.execute("LOAD 'age'")
        await db.execute("SET search_path = ag_catalog, '$user', public")
    except Exception:
        logger.info("AGE 扩展不可用，跳过图节点写入")
        return

    for article_id in article_ids:
        try:
            art = await db.fetch_one(
                "SELECT id, article_no, obligation_level, is_mandatory FROM regulation_articles WHERE id=$1",
                article_id,
            )
            if not art:
                continue

            cypher = (
                "SELECT * FROM cypher('cad_graph', $$"
                " MERGE (a:Article {id: '%s'})"
                " SET a.article_no='%s', a.obligation_level='%s', a.is_mandatory=%s"
                " RETURN id(a)"
                "$$) AS (node_id agtype)"
            ) % (
                article_id,
                str(art["article_no"]).replace("'", "''"),
                art["obligation_level"],
                "true" if art["is_mandatory"] else "false",
            )

            result = await db.fetch_one(cypher)
            if result:
                node_id = result[0]
                await db.execute(
                    "UPDATE regulation_articles SET age_node_id=$1 WHERE id=$2",
                    node_id, article_id,
                )
        except Exception as exc:
            logger.warning("AGE node for article %s failed: %s", article_id, exc)


# ── Chroma 向量化 ─────────────────────────────────────────────

async def vectorize_articles(
    db: Any,
    article_ids: list[str],
) -> None:
    """
    将条文内容写入 Chroma 向量库，失败时静默跳过（不影响主流程）。
    collection 名称：regulation_articles
    """
    try:
        import chromadb  # type: ignore
        from core.config import settings

        client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
        )
        collection = client.get_or_create_collection("regulation_articles")
    except Exception as exc:
        logger.info("Chroma 不可用，跳过向量化：%s", exc)
        return

    docs, ids, metas = [], [], []

    for article_id in article_ids:
        try:
            art = await db.fetch_one(
                "SELECT id, article_no, content, obligation_level, is_mandatory, book_id "
                "FROM regulation_articles WHERE id=$1",
                article_id,
            )
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
                "UPDATE regulation_articles SET vector_id=$1 WHERE id=$2",
                article_id, article_id,
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
    await _update_book_metadata(db, book_id, metadata)

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

    article_ids = await save_articles_to_db(db, book_id, articles)
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


async def _update_book_metadata(db: Any, book_id: str, metadata: dict[str, Any]) -> None:
    fields = {
        key: value
        for key, value in metadata.items()
        if value and key in {"title", "std_no", "version", "discipline", "publisher", "effective_at"}
    }
    if "effective_at" in fields:
        fields["effective_at"] = _parse_date(fields["effective_at"])
    if not fields:
        return
    sets = ", ".join(f"{key}=${idx + 2}" for idx, key in enumerate(fields))
    try:
        await db.execute(
            f"UPDATE regulation_books SET {sets}, updated_at=now() WHERE id=$1",
            book_id,
            *fields.values(),
        )
    except Exception as exc:
        logger.warning("update regulation book metadata failed: %s", exc)
