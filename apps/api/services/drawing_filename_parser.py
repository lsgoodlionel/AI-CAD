"""
图纸文件名智能解析器（蓝图 4.2）

从上传文件名中解析 {drawing_no, discipline, title, version}，
供批量上传 / ZIP 整套导入在前端元数据缺失时兜底使用。
解析不出的字段给安全默认值（discipline=general / version=A / drawing_no=文件名主干）。
"""
from dataclasses import dataclass
import re

# 专业前缀映射（按序匹配）：结施/GS→structure 建施/JS→architecture
# 水施|电施|暖施|机施/SS|DS|NS→mep 装施/ZS→decoration；无法判断→general
_DISCIPLINE_PREFIXES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("结施", "GS"), "structure"),
    (("建施", "JS"), "architecture"),
    (("水施", "电施", "暖施", "机施", "SS", "DS", "NS"), "mep"),
    (("装施", "ZS"), "decoration"),
)

# 专业全称关键词（前缀未命中时按包含匹配；机电类先查——"建筑电气"应归 mep）
_DISCIPLINE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("给排水", "电气", "暖通", "机电", "消防", "弱电"), "mep"),
    (("钢结构", "结构", "桩基", "人防", "基坑", "基础", "筏板", "底板", "承台"), "structure"),
    (("建筑", "幕墙", "景观"), "architecture"),
    (("装饰", "装修"), "decoration"),
)

# 多段图号（如 S-0-11-103C / S-0-31-102.01C）优先于简单图号
_MULTI_SEGMENT_NO_RE = re.compile(
    r"[A-Za-z]{1,3}(?:[-_]\d{1,4}(?:\.\d{1,2})?){2,4}[A-Za-z]?"
)
_DRAWING_NO_RE = re.compile(r"[A-Za-z一-龥]{1,4}[-_ ]?\d{1,4}")
_VERSION_RE = re.compile(r"[Vv]?([A-Z])(?:版|$)")
# 图号尾字母版次（103C → C）
_TRAILING_REV_RE = re.compile(r"\d([A-Z])$")
_SEPARATORS = " -_"
DEFAULT_VERSION = "A"
DEFAULT_DISCIPLINE = "general"

# ── 图种关键词（B-01 图种判别供料）────────────────────────────
# view_type ∈ {plan, section, elevation, detail}；unknown 由判别器在无证据时给出。
VIEW_TYPE_PLAN = "plan"
VIEW_TYPE_SECTION = "section"
VIEW_TYPE_ELEVATION = "elevation"
VIEW_TYPE_DETAIL = "detail"
VIEW_TYPE_UNKNOWN = "unknown"

# 关键词词表（中文用词不统一，集中维护便于扩充）：
# 剖面：含「剖」即命中（剖面/剖视/剖切/X-X剖），召回优先，漏判代价最高。
_VIEW_SECTION_RE = re.compile(r"剖|section", re.I)
# 立面：立面（含正/背/侧/东南西北立面）/ elevation。
_VIEW_ELEVATION_RE = re.compile(r"立面|elevation", re.I)
# 平面：平面 / plan / 楼层 / N层（一二三..或数字）/ 顶板。
_VIEW_PLAN_RE = re.compile(r"平面|plan|楼层|[一二三四五六七八九十百\d]+\s*层|顶板", re.I)
# 详图：兜底类，详图/大样/节点/做法/detail。
_VIEW_DETAIL_RE = re.compile(r"详图|大样|节点|做法|detail", re.I)

# 优先级：剖面 > 立面 > 平面 > 详图（详图为兜底，先满足剖/立面高召回）。
_VIEW_TYPE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_VIEW_SECTION_RE, VIEW_TYPE_SECTION),
    (_VIEW_ELEVATION_RE, VIEW_TYPE_ELEVATION),
    (_VIEW_PLAN_RE, VIEW_TYPE_PLAN),
    (_VIEW_DETAIL_RE, VIEW_TYPE_DETAIL),
)


@dataclass(frozen=True)
class ParsedField:
    value: str
    confidence: float
    span: tuple[int, int] | None
    source: str = "filename"


@dataclass(frozen=True)
class ParsedDrawingMetadata:
    drawing_no: ParsedField
    discipline: ParsedField
    title: ParsedField
    version: ParsedField


@dataclass(frozen=True)
class ViewTypeKeywordHit:
    """图种关键词命中结果（B-01 供料层）。"""
    view_type: str
    keyword: str
    span: tuple[int, int]


def match_view_type_keyword(text: str | None) -> ViewTypeKeywordHit | None:
    """按优先级（剖面>立面>平面>详图）匹配图种关键词。

    命中返回首个匹配（含匹配子串与位置），全不命中返回 None。
    仅做关键词判别，不含几何/VLM 佐证——由 drawing_view_classifier 编排。
    """
    if not text:
        return None
    for pattern, view_type in _VIEW_TYPE_RULES:
        match = pattern.search(text)
        if match:
            return ViewTypeKeywordHit(
                view_type=view_type, keyword=match.group(0), span=match.span()
            )
    return None


def parse_drawing_filename(filename: str) -> dict:
    """解析图纸文件名，返回 {drawing_no, discipline, title, version}。

    规则（按序）：
    1. 专业前缀映射（见 _DISCIPLINE_PREFIXES）
    2. 图号：首个 `[A-Za-z一-龥]{1,4}[-_ ]?\\d{1,4}` 匹配；无匹配→文件名主干
    3. 版本：`[Vv]?([A-Z])(?:版|$)`（含 _A/_B 结尾后缀）；无匹配→'A'
    4. title = 去除图号/版本标记后的剩余主干
    """
    evidence = parse_drawing_filename_evidence(filename)
    return {
        "drawing_no": evidence.drawing_no.value,
        "discipline": evidence.discipline.value,
        "title": evidence.title.value,
        "version": evidence.version.value,
    }


def parse_drawing_filename_evidence(filename: str) -> ParsedDrawingMetadata:
    stem = _extract_stem(filename)
    no_match = _MULTI_SEGMENT_NO_RE.search(stem) or _DRAWING_NO_RE.search(stem)
    drawing_no = no_match.group(0) if no_match else stem
    drawing_no_span = no_match.span() if no_match else None
    version, version_span = _detect_version(stem, drawing_no, drawing_no_span)
    discipline, discipline_confidence = _detect_discipline(stem)
    title = _build_title(stem, drawing_no_span, version_span)
    title_confidence = 0.8 if title and title != stem else 0.4 if title else 0.2
    return ParsedDrawingMetadata(
        drawing_no=ParsedField(
            value=drawing_no,
            confidence=0.95 if no_match else 0.45,
            span=drawing_no_span,
        ),
        discipline=ParsedField(
            value=discipline,
            confidence=discipline_confidence,
            span=None,
        ),
        title=ParsedField(
            value=title,
            confidence=title_confidence,
            span=None,
        ),
        version=ParsedField(
            value=version,
            confidence=0.9 if version_span else 0.3,
            span=version_span,
        ),
    )


def _extract_stem(filename: str) -> str:
    """去除路径与扩展名，得到文件名主干"""
    basename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    return stem.strip()


def _detect_discipline(stem: str) -> tuple[str, float]:
    """先按前缀缩写匹配，再按专业全称关键词包含匹配（机电类优先）"""
    upper = stem.upper()
    for prefixes, discipline in _DISCIPLINE_PREFIXES:
        if any(upper.startswith(prefix) for prefix in prefixes):
            return discipline, 0.9
    for keywords, discipline in _DISCIPLINE_KEYWORDS:
        if any(keyword in stem for keyword in keywords):
            return discipline, 0.75
    return DEFAULT_DISCIPLINE, 0.4


def _detect_version(
    stem: str,
    drawing_no: str,
    drawing_no_span: tuple[int, int] | None,
) -> tuple[str, tuple[int, int] | None]:
    """提取版次：图号尾字母（103C→C）优先，其次 _A/B版 等显式标记；无→A"""
    trailing = _TRAILING_REV_RE.search(drawing_no)
    if trailing:
        if drawing_no_span is None:
            return trailing.group(1), None
        start, _ = drawing_no_span
        return trailing.group(1), (start + trailing.start(1), start + trailing.end(1))
    match = _VERSION_RE.search(stem)
    if not match:
        return DEFAULT_VERSION, None
    return match.group(1), match.span()


def _build_title(
    stem: str,
    drawing_no_span: tuple[int, int] | None,
    version_span: tuple[int, int] | None,
) -> str:
    """从主干中剔除图号与版本标记，剩余部分作为标题"""
    title = stem
    spans = [span for span in (drawing_no_span, version_span) if span]
    for start, end in sorted(spans, key=lambda s: s[0], reverse=True):
        title = title[:start] + title[end:]
    return title.strip(_SEPARATORS)
