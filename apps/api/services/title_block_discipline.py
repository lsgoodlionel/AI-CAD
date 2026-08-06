"""从图框「专业」栏读取图纸专业(替代文件名猜测)。

**为什么**:专业原先靠文件名/标题关键词猜,实测大量错判——地质剖面/围护体图判成
`general` 或 `structure`,卫生间/厨房排水详图判成 `general`。而**图框里有「专业」这一栏**,
是设计单位填写的权威值。

**怎么做**:不重新 OCR。图框文字已在档案层(`drawing_extracted_info`,带 bbox),
按版式规律取值即可——**「专业」标签右侧同一行的第一个非标签文本**就是专业值
(实测该版式:标签 `专业` 右移约 48pt 处即 `给排水`)。

取到的值按**封闭词表**校验:图框专业是有限词汇,不在词表内一律判未知——
宁可留空让其他信号兜底,也不写入错值(错的专业会误导筛选与规则引擎选型)。
"""
from __future__ import annotations

from typing import Any

#: 图框内其他字段的标签(中英),扫描时须跳过,否则会把邻栏标签当成专业值
FIELD_LABELS = frozenset({
    "专业", "阶段", "图号", "比例", "日期", "会签", "图名", "项目", "工程编号",
    "设计", "校对", "审核", "审定", "制图", "版次",
    "DISCIPLINE", "STATUS", "SCALE", "DATE", "DRAWING NO.", "DRAWING NO",
    "JOB NO.", "JOB NO", "TITLE", "PROJECT",
    "NO.", "NO",          # 「DRAWING NO.」被 OCR 拆开后残留的尾巴
})

#: 图框专业词表 → 规范专业名。键为图框可能出现的写法,值为统一后的专业名。
DISCIPLINE_VOCAB: dict[str, str] = {
    "建筑": "建筑", "结构": "结构", "给排水": "给排水", "给水排水": "给排水",
    "排水": "给排水", "暖通": "暖通", "通风空调": "暖通", "暖通空调": "暖通",
    "电气": "电气", "强电": "电气", "弱电": "弱电", "智能化": "弱电",
    "消防": "消防", "幕墙": "幕墙", "精装": "精装", "装饰": "精装", "装修": "精装",
    "景观": "景观", "总图": "总图", "基坑围护": "基坑围护", "围护": "基坑围护",
    "岩土": "岩土", "地质": "岩土", "钢结构": "钢结构", "舞台机械": "舞台工艺",
    "舞台工艺": "舞台工艺", "声学": "声学", "室内": "精装", "人防": "人防",
}

#: 规范专业名 → 现有粗粒度枚举(规则引擎/建模按粗粒度选型,不能改)。
#: 取值必须落在 drawings.discipline 的 CHECK 约束内:
#: architecture / structure / mep / decoration / general —— 越界会写库失败。
COARSE_MAP: dict[str, str] = {
    "建筑": "architecture", "幕墙": "architecture",   # 幕墙属建筑外围护
    "精装": "decoration",
    "结构": "structure", "钢结构": "structure", "基坑围护": "structure",
    "岩土": "structure", "人防": "structure",
    "给排水": "mep", "暖通": "mep", "电气": "mep", "弱电": "mep", "消防": "mep",
    "景观": "general", "总图": "general", "声学": "general", "舞台工艺": "general",
}

_ROW_TOLERANCE_RATIO = 1.2   # 同行判定:纵向偏移不超过标签自身高度的倍数
_MAX_VALUE_LEN = 12          # 专业值很短;过长必是说明文字串入


def _center(bbox: list[float]) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


#: 英文标签模糊判定阈值:OCR 把 DRAWING NO. 糊成 DRAING/DRAMING/DRABING NO.,
#: 穷举写不完,按相似度识别。0.72 实测能收住这些变体又不误伤真实图号(P-29-22 等)。
_LABEL_SIMILARITY = 0.72

_ASCII_LABELS = tuple(
    lbl for lbl in FIELD_LABELS if lbl.isascii()
)


def is_field_label(text: str) -> bool:
    """是否是图框里其他字段的标签(应跳过而非当作值)。

    英文标签走模糊匹配——OCR 糊字(DRAING NO. / DISCIFLINE / DRABING NO.)穷举不完。
    """
    from difflib import SequenceMatcher

    s = text.strip().strip(":：").upper()
    if not s:
        return False
    if s in {lbl.upper() for lbl in FIELD_LABELS}:
        return True
    if not s.isascii():
        return False          # 中文标签必须精确,免得把中文图名判成标签
    compact = "".join(ch for ch in s if ch.isalpha())
    if len(compact) < 4:
        return False          # 太短的比不出相似度,交给精确匹配
    for lbl in _ASCII_LABELS:
        ref = "".join(ch for ch in lbl.upper() if ch.isalpha())
        if ref and SequenceMatcher(None, compact, ref).ratio() >= _LABEL_SIMILARITY:
            return True
    return False


def normalize_discipline(raw: str | None) -> str | None:
    """图框原文 → 规范专业名;不在词表内返回 None(宁缺勿错)。"""
    if not raw:
        return None
    s = raw.strip().strip(":：").replace(" ", "")
    if not s or len(s) > _MAX_VALUE_LEN:
        return None
    if s in DISCIPLINE_VOCAB:
        return DISCIPLINE_VOCAB[s]
    # OCR 常见粘连(如「专业给排水」「给排水专业」),按词表子串兜底取最长匹配
    hits = [v for k, v in DISCIPLINE_VOCAB.items() if k in s]
    if len(set(hits)) == 1:
        return hits[0]
    return None


def coarse_discipline(label: str | None) -> str | None:
    """规范专业名 → 现有粗粒度枚举(structure/architecture/mep/…)。"""
    return COARSE_MAP.get(label) if label else None


def has_cjk(text: str) -> bool:
    """是否含中日韩汉字。专业值必是中文,据此可跳过 OCR 糊掉的英文标签。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def find_field_value(
    items: list[tuple[str, list[float]]], label: str = "专业",
    *, require_cjk: bool = False,
) -> str | None:
    """图框文本 → 指定字段的值:标签**右侧同一行**的第一个非标签文本。

    items 为 (文本, bbox[x0,y0,x1,y1]) 列表。找不到标签或右侧无有效文本 → None。
    require_cjk=True 时只认含汉字的值——图框英文标签(DISCIPLINE)常被 OCR 糊成
    DISILIE / DISCIPLIME / DISCIRLIE,穷举写不完,按「专业值必含汉字」一刀切更稳。
    """
    labels = [b for text, b in items
              if text.strip().strip(":：") == label and len(b) >= 4]
    if not labels:
        return None
    box = labels[0]
    cx, cy = _center(box)
    tol = max(abs(box[3] - box[1]), 1.0) * _ROW_TOLERANCE_RATIO

    best: tuple[float, str] | None = None
    for text, b in items:
        if len(b) < 4 or text.strip().strip(":：") == label:
            continue
        x, y = _center(b)
        if x <= cx or abs(y - cy) > tol:
            continue
        if is_field_label(text):
            continue          # 邻栏标签(DISCIPLINE / 图号…)跳过,继续往右找
        if require_cjk and not has_cjk(text):
            continue          # OCR 糊掉的英文标签,不可能是专业值
        if best is None or x - cx < best[0]:
            best = (x - cx, text)
    return best[1] if best else None


_COLUMN_GAP_RATIO = 3.0   # 相邻 token 间距超过字高的这个倍数,视为跨到了下一栏


def _merge_overlapping(acc: str, nxt: str) -> str:
    """拼接时去掉重叠部分。

    OCR 会把同一段文字重复识别成互相重叠的 token(`IE-` + `IE-20-08`),
    直接拼会得到 `IE-IE-20-08`。取最大重叠后缀=前缀再拼。
    """
    limit = min(len(acc), len(nxt))
    for k in range(limit, 0, -1):
        if acc[-k:] == nxt[:k]:
            return acc + nxt[k:]
    return acc + nxt


def find_field_row(
    items: list[tuple[str, list[float]]], label: str,
    *, require_cjk: bool = False,
) -> str | None:
    """图框文本 → 字段值(**同行多个 token 拼接**)。

    图号常被 OCR 切成多段(`IE-2` + `1-02.5` + `B`),只取最近一个会截断。
    按 x 排序累加,**相邻间距超过字高 3 倍即认为跨栏,就此打住**。
    """
    labels = [b for text, b in items
              if text.strip().strip(":：") == label and len(b) >= 4]
    if not labels:
        return None
    box = labels[0]
    cx, cy = _center(box)
    height = max(abs(box[3] - box[1]), 1.0)
    tol = height * _ROW_TOLERANCE_RATIO

    row = sorted(
        (b[0], b[2], text) for text, b in items
        if len(b) >= 4 and _center(b)[0] > cx and abs(_center(b)[1] - cy) <= tol
        and text.strip().strip(":：") != label
        and not is_field_label(text)
        and (not require_cjk or has_cjk(text))
    )
    if not row:
        return None
    acc = row[0][2]
    prev_right = row[0][1]
    for x0, x1, text in row[1:]:
        if x0 - prev_right > height * _COLUMN_GAP_RATIO:
            break
        acc = _merge_overlapping(acc, text)
        prev_right = x1
    return acc.strip() or None


def drawing_no_from_items(items: list[tuple[str, list[float]]]) -> str | None:
    """图框「图号」栏 → 图号(同行拼接,去空白)。"""
    raw = find_field_row(items, "图号")
    if not raw:
        return None
    no = raw.replace(" ", "").strip()
    # 图号是字母数字加分隔符;含汉字必是串行到图名列了
    if has_cjk(no) or len(no) < 3 or len(no) > 32:
        return None
    return no


def discipline_from_items(items: list[tuple[str, list[float]]]) -> str | None:
    """图框文本 → 规范专业名(读值 + 词表校验一步到位)。"""
    return normalize_discipline(find_field_value(items, "专业", require_cjk=True))


# ── 仓储 ─────────────────────────────────────────────────────────

_ITEMS_SQL = """
SELECT drawing_id, content, location_json
FROM drawing_extracted_info
WHERE project_id = :project_id AND is_active AND location_json IS NOT NULL
"""

_UPDATE_SQL = """
UPDATE drawings SET discipline_label = :label, discipline = :coarse, updated_at = now()
WHERE id = :id
"""


def _bbox(location_json: Any) -> list[float]:
    import json
    loc = location_json
    if isinstance(loc, str):
        try:
            loc = json.loads(loc)
        except (ValueError, TypeError):
            return []
    if not isinstance(loc, dict):
        return []
    b = loc.get("bbox")
    return [float(v) for v in b] if isinstance(b, (list, tuple)) and len(b) >= 4 else []


async def backfill_project(db: Any, project_id: str, *, dry_run: bool = False) -> dict:
    """全项目按图框「专业」栏修正专业。返回 {读到, 写入, 各专业分布}。"""
    from collections import defaultdict

    by_drawing: dict[str, list[tuple[str, list[float]]]] = defaultdict(list)
    for row in await db.fetch_all(_ITEMS_SQL, {"project_id": project_id}):
        b = _bbox(row["location_json"])
        if b:
            by_drawing[str(row["drawing_id"])].append((row["content"] or "", b))

    found: dict[str, str] = {}
    for drawing_id, items in by_drawing.items():
        label = discipline_from_items(items)
        if label:
            found[drawing_id] = label

    updated = 0
    if not dry_run:
        for drawing_id, label in found.items():
            await db.execute(_UPDATE_SQL, {
                "id": drawing_id, "label": label,
                "coarse": coarse_discipline(label) or "general"})
            updated += 1

    dist: dict[str, int] = {}
    for label in found.values():
        dist[label] = dist.get(label, 0) + 1
    return {"scanned": len(by_drawing), "found": len(found),
            "updated": updated, "distribution": dist}
