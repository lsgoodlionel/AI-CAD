"""图例表 → 符号切图 + 中文名（训练标记数据的**强标签**来源）。

**为什么图例表是富矿**：全批资料实测有 107 页含「图例 / 符号 / 代号」表，
版式高度规整 —— `名称 | 图例 | 说明` 或 `序号 | 符号 | 说明` 三栏，
每行一个符号。符号格里是图、名称格里是中文名，**同一行就是一对标注**。
这比任何自动分类都强：名字是编者写下的，不是猜的。

**必须处理旋转**：横排表印在竖开本上时整表转 90°（实测电气图形符号表
p.24~p.29 全是这样），不处理的话 OCR 出来是
`'818188181178154172序号'` 这种把整列串成一行的乱码。

**判据全部几何、不猜**：
- 表线用形态学开运算取（长横线 / 长竖线），不靠阈值猜；
- 表头靠关键词定位（`名称/图例/符号/说明/序号`），定不到就**不出数据**，
  而不是假设「第二列是符号」—— 列序在不同书里并不一致。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 表头关键词 → 列语义。**顺序即优先级**（`图例`/`符号` 都指符号列）。
HEADER_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("名称", "name"), ("名 称", "name"),
    ("图例", "symbol"), ("符号", "symbol"), ("图形符号", "symbol"),
    ("说明", "note"), ("备注", "note"),
    ("序号", "index"), ("代号", "code"),
)

#: 直线检测的形态学核长度，按**单元格尺度**取：短于此的横竖笔画
#: （汉字的横、竖）不会被当成表线。以图像宽/高的比例给，跨分辨率稳定。
LINE_KERNEL_RATIO = 0.08

#: 表线的最小长度（占图像宽/高的比例）。低于此的是短分隔线或字。
MIN_LINE_RATIO = 0.15

#: 相邻线合并容差（像素）。扫描件的表线常有 1~3 px 的重影。
LINE_MERGE_TOL = 6

#: 单元格最小边长（像素，200dpi 下）。小于此的是线交点噪声。
MIN_CELL_PX = 18

#: 表块的最小面积（占整页比例）。一页常有**并排两个表**（实测材料图例页
#: 左右各一），把它们混进同一套网格会两败俱伤：左表的行线和右表的行线
#: 相互穿插，`名 称` 会被右表的边框竖线劈成两格，表头随之认错。
#: 所以先分块、块内再建网格。
MIN_TABLE_AREA_RATIO = 0.03

#: 表块的最小边长（像素）——细长条是页眉横线，不是表。
MIN_TABLE_SIDE_PX = 80

#: 符号格判据：格内 OCR 文字覆盖面积占比低于此值，且有墨 —— 才算符号格。
#: 不用「无文字」做判据：符号旁常带 `Wh`、`kW` 之类的标注文字。
MAX_TEXT_COVERAGE_FOR_SYMBOL = 0.35

#: 符号格的最小墨占比 —— 全白格是空格，不是符号。
MIN_INK_RATIO_FOR_SYMBOL = 0.005

#: 竖排判据：OCR token 中「高 > 宽」的比例超过此值，认为整页内容旋转了 90°。
ROTATED_TOKEN_RATIO = 0.55

#: 参与旋转判定的最少 token 数 —— 样本太少时不下结论，按未旋转处理。
ROTATION_MIN_TOKENS = 15

#: 名称的合理长度上限（汉字数）。超出的多半是**说明串进了名称格** ——
#: 合并单元格（rowspan）会被网格切碎，说明的片段落到名称位置。
MAX_NAME_CHARS = 12

#: 名称里不该出现的成分：条目编号 `(1)`、句读、跨行连接词。
_BAD_NAME_RE = re.compile(r"[（(]\s*[0-9]|[，。；、：]$|^[，。；、：]|等$")


@dataclass(frozen=True)
class Cell:
    row: int
    col: int
    bbox: tuple[int, int, int, int]      # 图像像素 (x0, y0, x1, y1)
    text: str = ""
    ink_ratio: float = 0.0
    text_coverage: float = 0.0

    @property
    def is_symbol(self) -> bool:
        return (self.ink_ratio >= MIN_INK_RATIO_FOR_SYMBOL
                and self.text_coverage <= MAX_TEXT_COVERAGE_FOR_SYMBOL)


@dataclass(frozen=True)
class LegendEntry:
    """一行 = 一个符号标注。"""
    source_key: str
    page_index: int
    table_index: int
    row: int
    name: str
    note: str = ""
    code: str = ""
    symbol_bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    rotated: bool = False
    dpi: int = 200
    warnings: tuple[str, ...] = ()
    extras: dict = field(default_factory=dict)

    @property
    def entry_id(self) -> str:
        return (f"{self.source_key}/p{self.page_index:04d}"
                f"t{self.table_index}r{self.row:03d}")

    def to_dict(self) -> dict:
        return {"entry_id": self.entry_id, "source_key": self.source_key,
                "page_index": self.page_index, "table_index": self.table_index,
                "row": self.row, "name": self.name, "note": self.note,
                "code": self.code, "symbol_bbox": list(self.symbol_bbox),
                "rotated": self.rotated, "dpi": self.dpi,
                "warnings": list(self.warnings), **self.extras}


# --------------------------------------------------------------------------
# 旋转判定
# --------------------------------------------------------------------------

def is_rotated(tokens: list[dict]) -> bool:
    """OCR token 形状 → 页面内容是否转了 90°。

    横排中文行的 bbox 是**宽 > 高**；整表转 90° 后每一行变成竖条，
    OCR 只能把它读成一串竖着的字，bbox 变成**高 > 宽**。
    样本太少不下结论 —— 宁可当作未旋转，也不要把正常页转坏。
    """
    shapes = []
    for t in tokens:
        bbox = t.get("b") or t.get("bbox") or []
        if len(bbox) < 4:
            continue
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w > 0 and h > 0:
            shapes.append(h > w)
    if len(shapes) < ROTATION_MIN_TOKENS:
        return False
    return sum(shapes) / len(shapes) >= ROTATED_TOKEN_RATIO


# --------------------------------------------------------------------------
# 表线 → 网格
# --------------------------------------------------------------------------

def _merge_positions(values: list[int], tol: int = LINE_MERGE_TOL) -> list[int]:
    """把彼此相邻的线位置并成一条（扫描重影会把一条线检出成两三条）。"""
    if not values:
        return []
    values = sorted(values)
    out = [[values[0]]]
    for v in values[1:]:
        if v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [int(sum(g) / len(g)) for g in out]


def _line_masks(binary):
    """→ (长横线掩码, 长竖线掩码)。核长按图尺度取，汉字的横竖笔画留不下。"""
    import cv2

    h, w = binary.shape[:2]
    h_len = max(int(w * LINE_KERNEL_RATIO), 12)
    v_len = max(int(h * LINE_KERNEL_RATIO), 12)
    return (
        cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1))),
        cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len))),
    )


def detect_tables(binary) -> list[tuple[int, int, int, int]]:
    """→ 各表块的外接框 [(x0, y0, x1, y1)]，按自上而下、自左而右排。

    只取表线构成的连通域：并排的两个表之间是空白，天然分得开。
    找不到合规表块时返回整页 —— 退化成原来的单块行为，不至于什么都不出。
    """
    import cv2

    h, w = binary.shape[:2]
    h_lines, v_lines = _line_masks(binary)
    mask = cv2.dilate(cv2.bitwise_or(h_lines, v_lines),
                      cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, cw, ch = cv2.boundingRect(contour)
        if cw < MIN_TABLE_SIDE_PX or ch < MIN_TABLE_SIDE_PX:
            continue
        if cw * ch < w * h * MIN_TABLE_AREA_RATIO:
            continue
        boxes.append((x, y, x + cw, y + ch))
    if not boxes:
        return [(0, 0, w, h)]
    return sorted(boxes, key=lambda b: (b[1] // 50, b[0]))


def detect_grid(binary) -> tuple[list[int], list[int]]:
    """二值图（墨=255）→ (水平线 y 列表, 垂直线 x 列表)。

    传入的应当是**单个表块**的 ROI（见 `detect_tables`）——
    `MIN_LINE_RATIO` 是相对传入图宽/高算的，整页传进来会把
    只跨半幅的表线判成短线丢掉。
    """
    import cv2
    import numpy as np

    h, w = binary.shape[:2]
    h_lines, v_lines = _line_masks(binary)

    ys = [int(y) for y in np.where(
        (h_lines > 0).sum(axis=1) >= w * MIN_LINE_RATIO)[0]]
    xs = [int(x) for x in np.where(
        (v_lines > 0).sum(axis=0) >= h * MIN_LINE_RATIO)[0]]
    # **ROI 的四边就是表格外框**（`detect_tables` 是按表线连通域切的），
    # 但它们贴在图像边缘，列和常因裁切少一两个像素而达不到阈值 ——
    # 漏掉左边框的后果是**整个「名称」列不成格**，符号配不上名字。
    ys = _merge_positions(ys + [0, h - 1])
    xs = _merge_positions(xs + [0, w - 1])
    return ys, xs


def _join_spans(spans: list[tuple[float, float, float, str]]) -> str:
    """格内文字片段 → 一串。**先按行带分组，再行内按 x 排**。

    直接按 (y, x) 排会出错：`金  属` 两个字在同一行，但 OCR 给出的
    y0 可以差几个像素，排序就把它读成 `属金`。行带按纵向重叠判定，
    对这种像素级抖动免疫。
    """
    if not spans:
        return ""
    spans = sorted(spans, key=lambda s: s[0])
    bands: list[list[tuple[float, float, float, str]]] = [[spans[0]]]
    for span in spans[1:]:
        top, bottom = bands[-1][0][0], max(s[1] for s in bands[-1])
        height = max(bottom - top, 1e-6)
        overlap = min(bottom, span[1]) - max(top, span[0])
        if overlap / height >= 0.5:
            bands[-1].append(span)
        else:
            bands.append([span])
    return "".join("".join(s[3] for s in sorted(band, key=lambda s: s[2]))
                   for band in bands).strip()


def build_cells(binary, ys: list[int], xs: list[int],
                tokens: list[dict]) -> list[Cell]:
    """网格线 → 单元格，并算每格的墨占比与文字覆盖。"""
    import numpy as np

    cells: list[Cell] = []
    for r in range(len(ys) - 1):
        for c in range(len(xs) - 1):
            y0, y1 = ys[r] + 2, ys[r + 1] - 2
            x0, x1 = xs[c] + 2, xs[c + 1] - 2
            if y1 - y0 < MIN_CELL_PX or x1 - x0 < MIN_CELL_PX:
                continue
            patch = binary[y0:y1, x0:x1]
            area = max(patch.size, 1)
            ink = float(np.count_nonzero(patch)) / area

            spans, covered = [], 0.0
            for t in tokens:
                b = t.get("b") or t.get("bbox") or []
                if len(b) < 4:
                    continue
                ix0, iy0 = max(b[0], x0), max(b[1], y0)
                ix1, iy1 = min(b[2], x1), min(b[3], y1)
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                inter = (ix1 - ix0) * (iy1 - iy0)
                own = max((b[2] - b[0]) * (b[3] - b[1]), 1e-6)
                if inter / own < 0.5:
                    continue                       # 不足一半在格内，不算这格的字
                spans.append((b[1], b[3], b[0], t.get("t") or t.get("text") or ""))
                covered += inter
            cells.append(Cell(
                row=r, col=c, bbox=(x0, y0, x1, y1),
                text=_join_spans(spans),
                ink_ratio=ink, text_coverage=covered / area))
    return cells


# --------------------------------------------------------------------------
# 表头 → 列语义
# --------------------------------------------------------------------------

_WS_RE = re.compile(r"[\s　]+")


def classify_columns(cells: list[Cell]) -> tuple[int, dict[int, str]]:
    """定位表头行并给出 {列号: 语义}。定不到表头返回 (-1, {})。

    **定不到就不出数据**：列序在不同书里不一致（材料图例是
    `名称|图例|说明`，电气符号表是 `序号|符号|说明`），
    假设某一列是符号列会系统性地标错。
    """
    by_row: dict[int, list[Cell]] = {}
    for cell in cells:
        by_row.setdefault(cell.row, []).append(cell)

    best: tuple[int, int, dict[int, str]] = (-1, 0, {})
    for row in sorted(by_row):
        mapping: dict[int, str] = {}
        for cell in by_row[row]:
            text = _WS_RE.sub("", cell.text)
            if not text or len(text) > 6:
                continue
            for keyword, role in HEADER_KEYWORDS:
                if _WS_RE.sub("", keyword) == text:
                    mapping[cell.col] = role
                    break
        # 表头必须同时有符号列和一个文字列，否则不是表头
        if "symbol" in mapping.values() and (
                {"name", "note", "index", "code"} & set(mapping.values())):
            if len(mapping) > best[1]:
                best = (row, len(mapping), mapping)
    return best[0], best[2]


def is_plausible_name(name: str) -> bool:
    """名称是否像个术语（而不是被切碎的说明）。"""
    text = _WS_RE.sub("", name or "")
    if not text or len(text) > MAX_NAME_CHARS:
        return False
    return not _BAD_NAME_RE.search(text)


def _pair_columns(roles: dict[int, str]) -> list[dict[str, int]]:
    """列语义 → 列组。**每个符号列配它左边最近的名称列**。

    中文图例表把 `名称` 写在 `图例` 之前，且一页常横向重复多组
    （实测材料图例页是 `名称|图例|说明|名称|图例|说明` 六列一张表）。
    只认第一个符号列会**丢掉右半张表**。
    """
    symbol_cols = sorted(c for c, r in roles.items() if r == "symbol")
    name_cols = sorted(c for c, r in roles.items() if r == "name")
    note_cols = sorted(c for c, r in roles.items() if r == "note")
    code_cols = sorted(c for c, r in roles.items() if r in ("code", "index"))

    # **整表没有「名称」列时，「说明」列就是名称** —— 这是表结构，不是缺陷。
    # 实测电气图形符号表是 `序号|符号|说明` 三栏（GB/T 4728 体例），
    # 说明栏写的正是「中性线」「屏蔽导体」这样的符号名。
    note_is_name = not name_cols and bool(note_cols)

    groups = []
    for i, sym in enumerate(symbol_cols):
        upper = symbol_cols[i + 1] if i + 1 < len(symbol_cols) else 10 ** 6
        left = [c for c in name_cols if c < sym]
        right = [c for c in note_cols if sym < c < upper]
        codes = [c for c in code_cols if c < sym]
        if note_is_name:
            groups.append({"symbol": sym, "name": right[0] if right else -1,
                           "note": -1, "code": codes[-1] if codes else -1,
                           "name_role": "note_column"})
        else:
            groups.append({"symbol": sym, "name": left[-1] if left else -1,
                           "note": right[0] if right else -1,
                           "code": codes[-1] if codes else -1,
                           "name_role": "name_column"})
    return groups


def extract_entries(source_key: str, page_index: int, table_index: int,
                    cells: list[Cell], *, rotated: bool,
                    dpi: int = 200) -> list[LegendEntry]:
    """网格 + 列语义 → 逐行逐组的标注。"""
    header_row, roles = classify_columns(cells)
    if header_row < 0:
        return []
    groups = _pair_columns(roles)
    if not groups:
        return []

    by_row: dict[int, dict[int, Cell]] = {}
    for cell in cells:
        by_row.setdefault(cell.row, {})[cell.col] = cell

    out: list[LegendEntry] = []
    for row in sorted(by_row):
        if row <= header_row:
            continue
        cols = by_row[row]
        for group in groups:
            symbol = cols.get(group["symbol"])
            if symbol is None or not symbol.is_symbol:
                continue
            name = cols[group["name"]].text if group["name"] in cols else ""
            note = cols[group["note"]].text if group["note"] in cols else ""
            code = cols[group["code"]].text if group["code"] in cols else ""

            warnings: list[str] = []
            if not name and note:
                # 本行名称格为空、说明格有字 —— 这是**本行**的缺口
                # （多因合并单元格被网格切碎），必须留痕。
                name, note = note, ""
                warnings.append("name_from_note")
            if not name:
                warnings.append("no_name")
            elif not is_plausible_name(name):
                # 不丢弃、只标记 —— 让下游按需过滤，也让人能看见问题在哪。
                warnings.append("name_suspect")
            out.append(LegendEntry(
                source_key=source_key, page_index=page_index,
                table_index=table_index, row=row, name=name.strip(),
                note=note.strip(), code=code.strip(),
                symbol_bbox=symbol.bbox, rotated=rotated, dpi=dpi,
                warnings=tuple(warnings),
                extras={"ink_ratio": round(symbol.ink_ratio, 4),
                        "symbol_col": group["symbol"],
                        "name_role": group.get("name_role", "name_column")}))
    return out
