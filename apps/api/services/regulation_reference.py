"""从图纸说明里抽取规范引用，并与规范库比对。

**需求**：「总说明里提到的规范**必须下载到本地**，不断积累成规范库，
实时对比更新最新版本，作为建模审图基础数据库之一」。

要做这件事，先得知道说明到底引用了哪些规范。这也把两个交付连起来：
成篇说明（`drawing_spec_text`）与规范库（`regulation_importer`）。

产物有三类，缺一不可：
- **matched**：说明引用且库里有，版本一致
- **missing**：说明引用但库里没有 —— **这才是「必须下载到本地」的清单**
- **version_mismatch**：库里有同一本但年份不同 —— 「实时对比更新最新版本」的入口。
  它既不是命中（版本不对）也不是缺失（书是有的），必须单列，
  合并到任何一边都会让人做错决定。
"""
from __future__ import annotations

import re
from typing import Any

#: 标准号前缀。与 `regulation_importer._STD_NO_RE` 同一套体系。
_PREFIXES = ("GB/T", "GB/Z", "GB", "JGJ/T", "JGJ", "CJJ/T", "CJJ",
             "JTG", "DL/T", "TB/T", "TB", "CECS", "JC/T", "JT/T")

#: 标准号：前缀 + 可选空格 + 编号 + 可选 `-年份`。
#: 编号至少 1 位、年份必须 4 位——不加这两条，`1-A`（轴号）、
#: `C30-C50`、`A-201555010`（图号）都会被抽成「标准」，
#: 误抽一条就往规范库里塞一个不存在的标准。
_REF_RE = re.compile(
    r"(?<![A-Za-z0-9/])(" + "|".join(p.replace("/", r"/") for p in _PREFIXES) +
        # 连字符要认全角 `－`(U+FF0D)：实测 `（GB50204－2015）` 只认半角时
    # 年份丢失，`GB 50204` 与 `GB 50204-2015` 被拆成两条。
    r")\s*(\d{1,6}(?:\.\d+)?)(?:\s*[-—－‐]\s*(\d{4}))?(?![0-9])",
    re.IGNORECASE)

#: 书名号里的规范名称，取紧挨标准号之前的那一个。
#: **不得跨行**：说明里书名与编号常分行排（两栏版面尤其如此），
#: 跨行取最近的书名号会张冠李戴——实测 `GB 50010-2010` 被标成
#: 「地基处理技术规范」。补库清单上挂错书名会让人去下错的规范，
#: 宁可不写。
#: 允许书名与编号之间隔一个左括号——真实写法是「《名称》（GB50010-2010）」。
#: 结尾用 `\Z` 而非 `$`：Python 的 `$` **也匹配结尾换行之前**，
#: 用它写「不得跨行」根本没生效（实测两栏对照表里 `GB 50010-2010`
#: 仍被贴上上一行的《地基处理技术规范》）。
_TITLE_RE = re.compile(r"《([^《》\n]{2,40})》[ \t]*[（(]?[ \t]*\Z")


def _canonical(prefix: str, number: str) -> str:
    """前缀大写 + 单空格 + 编号。说明里写 `GB50010`、库里存 `GB 50010`，
    不归一就永远匹配不上。"""
    return f"{prefix.upper()} {number}"


#: 无年份佐证时，编号至少要有几位。**实测**真实说明里抽出 `GB 5`
#: （8 次，标着《建筑抗震设计规范》）——原文是 `GB 50011`，
#: OCR 把 `0` 认成字母 `O` 后编号被截断。往规范库塞一个不存在的标准，
#: 比漏掉一条更糟：它会一直挂在「待下载」清单上误导人。
MIN_DIGITS_WITHOUT_YEAR = 4

#: 标准年份的合理区间。**实测**清单里出现过 `GB 50010-9428`（6 次）——
#: OCR 把年份认花了。假年份比没有年份更糟：它会被当成一个真实存在的
#: 版本，既挂在补库清单上，又让真正的 `GB 50010-2010` 合并不进来。
MIN_STANDARD_YEAR = 1980
MAX_STANDARD_YEAR = 2035


def extract_references(text: str | None) -> list[dict]:
    """说明正文 → 规范引用列表（已去重、带出现次数）。

    一篇说明里同一本会被引用多次（「按 GB 50010 配筋」「锚固长度按
    GB50010 表 8.3.1」），去重后记次数——次数就是补库的优先级。
    """
    body = text or ""
    found: dict[str, dict] = {}
    for match in _REF_RE.finditer(body):
        number = match.group(2)
        year = match.group(3)
        if year is not None and not (
                MIN_STANDARD_YEAR <= int(year) <= MAX_STANDARD_YEAR):
            year = None                   # OCR 认花的年份，按无年份处理
        if year is None and len(number.split(".")[0]) < MIN_DIGITS_WITHOUT_YEAR:
            continue
        std_no = _canonical(match.group(1), number)
        full = f"{std_no}-{year}" if year else std_no
        title_match = _TITLE_RE.search(body[: match.start()])
        entry = found.get(full)
        if entry is None:
            found[full] = {
                "std_no": full,
                "title": title_match.group(1) if title_match else None,
                "year": year,
                "count": 1,
            }
        else:
            entry["count"] += 1
            if entry["title"] is None and title_match:
                entry["title"] = title_match.group(1)
    return list(found.values())


def _normalise(std_no: str | None) -> str:
    """去掉全部空白后大写——比对时唯一可靠的形态。"""
    return re.sub(r"\s+", "", str(std_no or "")).upper()


def _base_of(std_no: str) -> str:
    """去掉年份的编号主体，用于识别「同一本但版本不同」。"""
    return _normalise(std_no).rsplit("-", 1)[0]


def match_against_library(references: list[dict] | None,
                          library: list[Any] | None) -> dict:
    """引用清单 × 规范库 → matched / missing / version_mismatch。"""
    books = [dict(b) for b in (library or [])]
    by_full = {_normalise(b.get("std_no")): b for b in books if b.get("std_no")}
    by_base: dict[str, dict] = {}
    for book in books:
        if book.get("std_no"):
            by_base.setdefault(_base_of(book["std_no"]), book)

    matched, missing, mismatch = [], [], []
    for ref in references or []:
        key = _normalise(ref.get("std_no"))
        if key in by_full:
            matched.append({**ref, "book": by_full[key]})
            continue
        book = by_base.get(_base_of(ref.get("std_no", "")))
        if book is not None:
            # 库里有同一本但年份不同——既非命中也非缺失。
            mismatch.append({**ref, "library_std_no": book.get("std_no"),
                             "book": book})
            continue
        missing.append(ref)

    # 按被引用次数排序：先补引用最多的那本。
    missing.sort(key=lambda r: (-int(r.get("count") or 0), r.get("std_no") or ""))
    matched.sort(key=lambda r: (-int(r.get("count") or 0), r.get("std_no") or ""))
    return {"matched": matched, "missing": missing,
            "version_mismatch": mismatch}


def consolidate_references(references: list[dict] | None) -> list[dict]:
    """把无年份的引用并进同编号带年份的那条。

    **实测**：同一篇说明里 `GB 50204`（7 次）与 `GB 50204-2015`（4 次）
    分列两行——同一本规范被拆成两条，补库清单虚长，计数也散了。

    有多个年份候选时并到**最新版本**：说明通常指现行版。
    同一编号的不同年份**保持分列**——「实时对比更新最新版本」要靠它。
    没有带年份的同伴时原样保留，不能因为信息不全就丢掉。
    """
    items = [dict(r) for r in (references or [])]
    dated: dict[str, list[dict]] = {}
    for ref in items:
        if ref.get("year"):
            dated.setdefault(_base_of(ref["std_no"]), []).append(ref)
    for group in dated.values():
        group.sort(key=lambda r: str(r.get("year")), reverse=True)

    result: list[dict] = []
    for ref in items:
        if ref.get("year"):
            result.append(ref)
            continue
        candidates = dated.get(_normalise(ref["std_no"]))
        if not candidates:
            result.append(ref)
            continue
        newest = candidates[0]
        newest["count"] = int(newest.get("count") or 0) + int(ref.get("count") or 0)
        if not newest.get("title"):
            newest["title"] = ref.get("title")
    result.sort(key=lambda r: (-int(r.get("count") or 0), r.get("std_no") or ""))
    return result


_SPEC_TEXT_SQL = """
SELECT content FROM drawing_extracted_info
WHERE project_id = CAST(:project_id AS uuid)
  AND category = 'spec_text' AND is_active
"""

#: 带坐标的原始碎片——列级配对要靠位置，成篇说明里没有位置。
_POSITIONED_SQL = """
SELECT content, location_json FROM drawing_extracted_info
WHERE project_id = CAST(:project_id AS uuid) AND is_active
  AND category IN ('note', 'other', 'title')
  AND content LIKE '%《%'
"""

_LIBRARY_SQL = """
SELECT std_no, title FROM regulation_books
WHERE std_no IS NOT NULL AND status = 'active'
"""


async def project_regulation_coverage(db: Any, project_id: str) -> dict:
    """整项目说明 × 规范库 → 覆盖情况。

    `missing` 就是需求要的**「必须下载到本地」清单**，按被引用次数排序。
    """
    rows = await db.fetch_all(_SPEC_TEXT_SQL, {"project_id": project_id})
    merged: dict[str, dict] = {}
    for row in rows:
        for ref in extract_references(dict(row).get("content")):
            slot = merged.get(ref["std_no"])
            if slot is None:
                merged[ref["std_no"]] = dict(ref)
            else:
                slot["count"] += ref["count"]
                slot["title"] = slot["title"] or ref["title"]
    # **列级配对补书名**：成篇说明丢了位置，而规范引用常排成两栏对照表
    # （左栏书名、右栏编号）。用带坐标的原始碎片按行对齐配对，
    # 把「同一行取不到书名」时留空的那些补回来。
    from services.drawing_spec_text import tokens_from_archive

    positioned = tokens_from_archive(
        await db.fetch_all(_POSITIONED_SQL, {"project_id": project_id}))
    for ref in pair_references_by_column(positioned):
        slot = merged.get(ref["std_no"])
        if slot is not None and not slot.get("title") and ref.get("title"):
            slot["title"] = ref["title"]

    references = consolidate_references(list(merged.values()))
    library = [dict(b) for b in await db.fetch_all(_LIBRARY_SQL)]
    result = match_against_library(references, library)
    result["summary"] = {
        "spec_blocks": len(rows),
        "referenced": len(references),
        "citations": sum(int(r.get("count") or 0) for r in references),
        "library_books": len(library),
        "matched": len(result["matched"]),
        "missing": len(result["missing"]),
        "version_mismatch": len(result["version_mismatch"]),
    }
    return result


#: 两栏配对时允许的行基线偏差（pt）。两栏不会像素级对齐，
#: 但差得太远就不是同一行——**宁可不配对，也不要配错**：
#: 补库清单上挂错书名会让人去下错的规范。
MAX_ROW_OFFSET_PT = 12.0

#: 书名号里的规范名称（不限位置，供列级配对用）。
_TITLE_ANY_RE = re.compile(r"《([^《》\n]{2,40})》")


def pair_references_by_column(tokens: list[dict] | None) -> list[dict]:
    """带坐标的文本 → 规范引用（书名与编号**按行对齐**配对）。

    **为什么不能按阅读顺序**：规范引用常排成两栏对照表，
    左栏书名、右栏编号。按栏重建会把两列串成一列，
    「最近的书名」于是跨了行——实测 `GB 50010-2010` 被贴上
    上一行的《地基处理技术规范》（实为《混凝土结构设计规范》）。

    这里改用**位置**：编号 token 找 y 最接近的书名 token。
    差得超过 `MAX_ROW_OFFSET_PT` 就留空——有书名没编号的行不产出，
    也不为没配上的编号凭空造书名。
    """
    items = [t for t in (tokens or [])
             if isinstance(t, dict) and t.get("x") is not None
             and t.get("y") is not None and str(t.get("text") or "").strip()]
    titles = [(float(t["y"]), float(t["x"]), m.group(1))
              for t in items
              for m in [_TITLE_ANY_RE.search(str(t["text"]))] if m]

    out: list[dict] = []
    for token in items:
        text = str(token["text"])
        same_line = _TITLE_ANY_RE.search(text)
        for ref in extract_references(text):
            title = ref.get("title")
            if title is None and same_line:
                title = same_line.group(1)
            if title is None and titles:
                y = float(token["y"])
                best = min(titles, key=lambda t: abs(t[0] - y))
                if abs(best[0] - y) <= MAX_ROW_OFFSET_PT:
                    title = best[2]
            out.append({**ref, "title": title})
    return out
