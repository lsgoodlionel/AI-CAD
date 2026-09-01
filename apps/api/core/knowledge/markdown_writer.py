"""页文本 → 可读可核对的 Markdown（第②层「识别全文」）。

对齐规范库既有的**人机双读三层**（migration 048）：
① PDF 原件 ② 识别全文 ③ 结构化条文。本模块产出第②层。

**诚实纪律**（这批资料 92% 是扫描件，全靠 OCR）：
- frontmatter 必须记 `extract_method` / `ocr_dpi` / `mean_confidence`，
  人一眼能看出这份文字的来路，而不是把 OCR 结果当原文引用；
- 低置信页要**点名**（`low_confidence_pages`），不是埋在平均值里；
- 缺页要如实标 `missing_pages`，绝不用空串填充冒充完整。

**侧边栏升格为章节标识**：图集每页左右印竖排章节条（「总则」「平法制图规则」），
本是 OCR 的噪声来源，这里提出来做页标题 —— 它是「本页属于哪一章」的
直接证据，比从正文猜可靠。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

#: 平均置信度低于此值的页列入 `low_confidence_pages`，提示人工核对。
PAGE_CONFIDENCE_ALERT = 0.90

#: 正文字数少于此值的页视为「近乎空页」（纯图页/插页），单独统计。
#: 不是错误 —— 图集本来就有大量纯构造详图页。
NEAR_EMPTY_CHARS = 40


def _yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if any(ch in text for ch in ':#\'"\n[]{}|>') or text.strip() != text:
        return json.dumps(text, ensure_ascii=False)
    return text


def _frontmatter(data: dict) -> str:
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}: [" + ", ".join(
                    _yaml_scalar(v) for v in value) + "]")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _provenance_note(source, stats: dict) -> str:
    """正文开头的来路声明。**这段不是客套话** —— 它是引用者判断
    「这句话能不能直接当规范原文用」的唯一依据。"""
    if source.extract_method == "ocr":
        conf = stats.get("mean_confidence")
        conf_text = f"平均置信度 {conf:.3f}" if conf is not None else "置信度未知"
        body = (
            f"本文件由**扫描件 OCR** 得到（RapidOCR PP-OCRv6，"
            f"{stats.get('ocr_dpi', 200)} dpi，{conf_text}），**未经人工逐字校对**。\n"
            f"> 作为规范条文引用前，必须对照原件核对："
            f"`{source.filename}`。"
        )
    elif source.extract_method == "text_layer":
        body = (f"本文件取自 PDF **文本层**（非 OCR），字形准确；"
                f"多栏页经栏序恢复后重排。原件：`{source.filename}`。")
    else:
        body = (f"本文件取自 **EPUB** 结构化正文，字形准确。"
                f"原件：`{source.filename}`。")

    low = stats.get("low_confidence_pages") or []
    missing = stats.get("missing_pages") or []
    extra = ""
    if low:
        extra += (f"\n>\n> ⚠ **{len(low)} 页识别置信度偏低**"
                  f"（< {PAGE_CONFIDENCE_ALERT}），优先核对："
                  f"{', '.join('p.' + str(p + 1) for p in low[:20])}"
                  f"{' …' if len(low) > 20 else ''}。")
    if missing:
        extra += (f"\n>\n> ⚠ **缺 {len(missing)} 页**未抽取："
                  f"{', '.join('p.' + str(p + 1) for p in missing[:20])}"
                  f"{' …' if len(missing) > 20 else ''}。")
    return f"> **文字来路**：{body}{extra}"


def build_stats(source, pages: list) -> dict:
    """统计。`pages` 为 PageText 或等价 dict 的列表。"""
    def get(page, name, default=None):
        return getattr(page, name, None) if not isinstance(page, dict) \
            else page.get(name, default)

    confs = [c for c in (get(p, "confidence") for p in pages) if c is not None]
    seen = {get(p, "index") for p in pages}
    missing = [i for i in range(source.pages) if i not in seen]
    low = sorted(get(p, "index") for p in pages
                 if (get(p, "confidence") or 1.0) < PAGE_CONFIDENCE_ALERT)
    near_empty = [get(p, "index") for p in pages
                  if len((get(p, "text") or "").strip()) < NEAR_EMPTY_CHARS]
    return {
        "extracted_pages": len(pages),
        "declared_pages": source.pages,
        "missing_pages": missing,
        "char_count": sum(len(get(p, "text") or "") for p in pages),
        "mean_confidence": round(sum(confs) / len(confs), 4) if confs else None,
        "low_confidence_pages": low,
        "near_empty_pages": near_empty,
        "ocr_dpi": (get(pages[0], "dpi")
                    or (get(pages[0], "extras", {}) or {}).get("dpi", 200)
                    if pages else 200),
    }


def render_markdown(source, pages: list, stats: dict | None = None) -> str:
    """渲染全书 Markdown。"""
    def get(page, name, default=None):
        return getattr(page, name, default) if not isinstance(page, dict) \
            else page.get(name, default)

    stats = stats or build_stats(source, pages)
    fm = {
        "key": source.key,
        "std_no": source.std_no,
        "title": source.title,
        "kind": source.kind,
        "discipline": source.discipline,
        "declared_pages": source.pages,
        "extracted_pages": stats["extracted_pages"],
        "extract_method": source.extract_method,
        "ocr_dpi": stats["ocr_dpi"] if source.extract_method == "ocr" else None,
        "ocr_backend": ("rapidocr-PP-OCRv6"
                        if source.extract_method == "ocr" else None),
        "mean_confidence": stats["mean_confidence"],
        "low_confidence_pages": stats["low_confidence_pages"],
        "missing_pages": stats["missing_pages"],
        "near_empty_pages_count": len(stats["near_empty_pages"]),
        "char_count": stats["char_count"],
        "source_file": source.filename,
        "identified_by": source.identified_by,
        "supersedes": source.supersedes,
        "superseded_by": source.superseded_by,
        "generated_by": "core.knowledge.markdown_writer",
    }

    parts = [_frontmatter(fm), ""]
    heading = f"# {source.std_no + ' ' if source.std_no else ''}{source.title}"
    parts += [heading, "", _provenance_note(source, stats), ""]
    if source.notes:
        parts += [f"> **用途**：{source.notes}", ""]

    unit = "节" if source.extract_method == "epub" else "p."
    for page in pages:
        idx = get(page, "index", 0)
        text = (get(page, "text") or "").strip()
        sidebar = list(get(page, "sidebar") or [])
        conf = get(page, "confidence")

        label = f"## {unit}{idx + 1}"
        if sidebar:
            label += "　〔" + " / ".join(sidebar) + "〕"
        marks = []
        if conf is not None and conf < PAGE_CONFIDENCE_ALERT:
            marks.append(f"低置信 {conf:.2f}")
        if len(text) < NEAR_EMPTY_CHARS:
            marks.append("纯图页/近空")
        if marks:
            label += f"　`{'; '.join(marks)}`"
        parts += [label, "", text or "*（本页无可识别文字，内容为图）*", ""]

    return "\n".join(parts).rstrip() + "\n"


def write_book(source, pages: list, out_root: Path) -> dict:
    """落盘 `<out_root>/<key>/book.md` + `meta.json`。返回统计。"""
    stats = build_stats(source, pages)
    out_dir = out_root / source.key
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "book.md").write_text(
        render_markdown(source, pages, stats), encoding="utf-8")
    meta = {"source": {k: v for k, v in asdict(source).items()
                       if k not in ("evidence",)},
            "evidence": source.evidence, "stats": stats}
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats
