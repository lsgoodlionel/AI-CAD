"""图纸目录解析与默认排序(按图纸目录组织整套图)。

**目标**:图纸列表默认「目录在最前,其余按目录顺序」,并能展开一棵目录树直接跳图。

**目录从哪来**:图纸目录本身就是几张图(实测 69 张),其表格已被 OCR 进档案层。
按表头 `DRAWING NAME / 图名` 定位图名列,取其下方按 y 排序的条目 = 目录顺序。

**诚实的覆盖边界**:目录图的 OCR 是稀疏的——69 张目录图实测只提出约 377 条有效条目,
后缀匹配上约 193 张图(全项目 2311 张的 8%)。因此排序**不能只靠目录**,分三层:

    0 目录图本身  →  1 目录列出的图(按目录顺序)  →  2 其余(按专业 + 图号自然序)

第 3 层用图号自然序(`A-10-13.4` < `A-40-41` < `S-0-20-202.01C`),这是图纸集本来的
组织方式,覆盖 100%,不会因 OCR 稀疏而乱序。
"""
from __future__ import annotations

import re
from typing import Any

#: 图名列表头(中英,OCR 常粘连成 DRAWINGNAME)
NAME_HEADERS = frozenset({"DRAWINGNAME", "图名", "图纸名称", "DRAWINGSNAME"})
#: 图名列右边界的下一列表头(版本列),用于框定列宽
NEXT_COL_PREFIX = "VERSION"

_DEFAULT_COL_WIDTH = 520.0   # 找不到下一列表头时的列宽兜底(pt)
_COL_LEFT_SLACK = 18.0       # 图名左边略有出格,放宽一点
_ROW_TOLERANCE = 6.0         # 同一行的纵向容差(pt):目录行距远大于此
_MIN_ENTRY_LEN = 4           # 过短的条目是「（三）」这类续行碎片,不作独立条目

#: 排序层级
RANK_DIRECTORY = 0           # 目录图本身
RANK_LISTED = 1              # 目录列出的图
RANK_UNLISTED = 2            # 目录未覆盖的图


#: 含「目录」但其实是普通图纸的排除词(如「目录索引平面图」)
_NOT_DIRECTORY_HINTS = ("平面图", "剖面图", "立面图", "详图", "系统图", "大样", "配筋")


def is_directory_sheet(title: str | None) -> bool:
    """是否是图纸目录图。

    仅凭「目录」二字会把「目录索引平面图」这类误判成目录,故排除带明确图种词的标题。
    """
    if not title:
        return False
    if any(h in title for h in _NOT_DIRECTORY_HINTS):
        return False
    return "目录" in title or "DRAWING LIST" in title.upper()


def normalize_title(text: str | None) -> str:
    """标题归一:去空白/括号/连字符,便于目录条目与实际图名比对。"""
    return re.sub(r"[\s()（）\-—_,、.]", "", text or "").lower()


def natural_key(drawing_no: str | None) -> str:
    """图号 → 可字典序比较的自然序键(数字段左补零)。

    `A-10-13.4` < `A-40-41` < `S-0-20-202.01C`——按段比较,数字段按数值大小。
    """
    if not drawing_no:
        return "￿"           # 无图号沉底
    parts = re.split(r"(\d+)", drawing_no.strip().upper())
    return "".join(p.zfill(8) if p.isdigit() else p for p in parts)


def extract_entries(items: list[tuple[str, list[float]]]) -> list[str]:
    """目录图的文字 → 图名列条目(按 y 自上而下,即目录顺序)。

    items 为 (文本, bbox) 列表。找不到图名列表头 → []。
    同一行的碎片按 x 拼回一条(如「消火栓系统原理图」+「(二)」)。
    """
    headers = [b for text, b in items
               if len(b) >= 4 and text.replace(" ", "").upper() in NAME_HEADERS]
    if not headers:
        return []
    header = headers[0]
    left, top = header[0] - _COL_LEFT_SLACK, header[3]
    right = min(
        (b[0] for text, b in items
         if len(b) >= 4 and text.replace(" ", "").upper().startswith(NEXT_COL_PREFIX)),
        default=header[0] + _DEFAULT_COL_WIDTH,
    )

    cells = sorted(
        (b[1], b[0], text) for text, b in items
        if len(b) >= 4 and b[1] > top and left < b[0] < right - 10
    )
    rows: list[list[tuple[float, float, str]]] = []
    for cell in cells:
        if rows and cell[0] - rows[-1][0][0] <= _ROW_TOLERANCE:
            rows[-1].append(cell)
        else:
            rows.append([cell])

    entries = ["".join(c[2] for c in sorted(row, key=lambda c: c[1])) for row in rows]
    return [e for e in entries if len(normalize_title(e)) >= _MIN_ENTRY_LEN]


def match_entry(entry: str, index: dict[str, list[tuple[str, str]]]) -> str | None:
    """目录条目 → 图纸 id。

    实测库里图名带前缀(`给排水-竣工图--自动喷水灭火系统原理图(二)`),而目录只写
    后半段,故**精确匹配失败后按后缀匹配**;多个候选取标题最短者(多余前缀最少)。
    """
    key = normalize_title(entry)
    if not key:
        return None
    if key in index:
        return index[key][0][1]
    hits = [(title, did) for norm_title, lst in index.items()
            if norm_title.endswith(key) for title, did in lst]
    if not hits:
        return None
    return min(hits, key=lambda h: len(h[0]))[1]


def build_title_index(
    drawings: list[dict],
) -> dict[str, list[tuple[str, str]]]:
    """实际图纸 → {归一化标题: [(原标题, id)]},供目录条目比对。"""
    index: dict[str, list[tuple[str, str]]] = {}
    for d in drawings:
        title = d.get("title") or d.get("drawing_no") or ""
        key = normalize_title(title)
        if key:
            index.setdefault(key, []).append((title, str(d["id"])))
    return index


def assign_order(
    drawings: list[dict], sheet_entries: list[tuple[str, list[str]]],
) -> list[dict]:
    """图纸 + 各目录图的条目 → 每张图的排序三元组。

    sheet_entries 为 [(目录图 id, 该图的条目列表)],已按目录图自身顺序排列。
    返回 [{id, sort_rank, directory_seq, sort_key, directory_sheet_id}]。
    """
    index = build_title_index(drawings)
    seq_of: dict[str, tuple[int, str]] = {}     # drawing_id → (全局序号, 目录图 id)
    seq = 0
    for sheet_id, entries in sheet_entries:
        for entry in entries:
            did = match_entry(entry, index)
            seq += 1
            if did and did not in seq_of and did != sheet_id:
                seq_of[did] = (seq, sheet_id)

    out: list[dict] = []
    for d in drawings:
        did = str(d["id"])
        if is_directory_sheet(d.get("title")):
            rank, dseq, sheet = RANK_DIRECTORY, None, None
        elif did in seq_of:
            rank, (dseq, sheet) = RANK_LISTED, seq_of[did]
        else:
            rank, dseq, sheet = RANK_UNLISTED, None, None
        # 目录图之间按标题自然序(「图纸目录2」在「图纸目录12」之前);
        # 其余图纸按图号自然序——图号才是图纸集的编排依据
        tie = natural_key(d.get("title")) if rank == RANK_DIRECTORY \
            else natural_key(d.get("drawing_no"))
        out.append({
            "id": did, "sort_rank": rank, "directory_seq": dseq,
            "directory_sheet_id": sheet,
            "sort_key": f"{d.get('discipline_label') or d.get('discipline') or ''}|{tie}",
        })
    return out


# ── 仓储 ─────────────────────────────────────────────────────────

_DRAWINGS_SQL = """
SELECT id, drawing_no, title, discipline, discipline_label
FROM drawings WHERE project_id = :project_id
"""

_ITEMS_SQL = """
SELECT drawing_id, content, location_json
FROM drawing_extracted_info
WHERE project_id = :project_id AND is_active AND location_json IS NOT NULL
"""

_UPDATE_SQL = """
UPDATE drawings SET sort_rank = :rank, directory_seq = :seq,
    directory_sheet_id = CAST(:sheet AS uuid), sort_key = :key
WHERE id = :id
"""


async def rebuild_directory(db: Any, project_id: str, *, dry_run: bool = False) -> dict:
    """重建全项目图纸目录顺序。返回 {目录图数, 条目数, 关联数, 未关联数}。"""
    from collections import defaultdict

    from services.title_block_discipline import _bbox

    drawings = [dict(r) for r in await db.fetch_all(
        _DRAWINGS_SQL, {"project_id": project_id})]
    by_drawing: dict[str, list[tuple[str, list[float]]]] = defaultdict(list)
    for row in await db.fetch_all(_ITEMS_SQL, {"project_id": project_id}):
        b = _bbox(row["location_json"])
        if b:
            by_drawing[str(row["drawing_id"])].append((row["content"] or "", b))

    # 目录图按标题自然序遍历,保证目录内条目的全局序号也是自然顺序
    sheets = sorted(
        (d for d in drawings if is_directory_sheet(d.get("title"))),
        key=lambda d: natural_key(d.get("title")),
    )
    sheet_entries = [(str(s["id"]), extract_entries(by_drawing.get(str(s["id"]), [])))
                     for s in sheets]
    orders = assign_order(drawings, sheet_entries)

    if not dry_run:
        for o in orders:
            await db.execute(_UPDATE_SQL, {
                "id": o["id"], "rank": o["sort_rank"], "seq": o["directory_seq"],
                "sheet": o["directory_sheet_id"], "key": o["sort_key"]})

    entries = sum(len(e) for _, e in sheet_entries)
    linked = sum(1 for o in orders if o["sort_rank"] == RANK_LISTED)
    return {
        "sheets": len(sheets), "entries": entries, "linked": linked,
        "unlinked_entries": max(entries - linked, 0),
        "unlisted_drawings": sum(1 for o in orders if o["sort_rank"] == RANK_UNLISTED),
    }
