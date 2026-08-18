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

# ── 「专业-阶段-」前缀剥离（第二工程交叉验证暴露的缺陷）───────────
#
# 中文工程图纸文件名的常见结构：`{专业}-{阶段}-{图号}-{图名}`。
# 实测大歌剧院 **2166/2309（93.8%）** 的 title 混着这段前缀，
# 轨道交通更把「竣工图-01」整个当成了图号（阶段词 + 序号）。
#
# 阶段词是**行业通用术语**（兜底标准是国标），不是某工程的命名习惯。
_STAGE_WORDS: frozenset[str] = frozenset({
    "竣工图", "竣工", "施工图", "施工", "设计图", "初设", "初步设计",
    "招标图", "招标", "投标图", "深化图", "深化设计", "变更图", "蓝图",
    "方案图", "扩初", "扩初图", "送审图", "报批图", "复用图",
})

# 专业**全称**（用于剥离前缀段）。
#
# **只剥全称，绝不剥专业代码**：`结施-01`、`GS-101` 里的「结施」「GS」
# 是**图号的组成部分**（专业代码 + 序号），剥掉图号就没了；
# 而「结构-竣工图-S-31-07A-…」里的「结构」是独立的前缀段。
# 我第一版把 `_DISCIPLINE_PREFIXES`（代码）也放进来，打断了 5 个既有断言 ——
# 两者形似而角色相反，是这个模块最容易混淆的一处。
_DISCIPLINE_SEGMENT_WORDS: frozenset[str] = frozenset(
    word for words, _key in _DISCIPLINE_KEYWORDS for word in words
) | frozenset({"基坑支护", "景观图纸", "总图", "室内", "精装", "园林"})


def _strip_leading_prefix_segments(stem: str) -> str:
    """从左逐段剥离「专业」「阶段」段；遇到第一个非此类段即停。

    **只剥前缀**是关键：图名里的「设计说明」「竣工验收说明」不能被吃掉，
    因为扫描在遇到图号段（或任何非专业/阶段段）时就停了。
    """
    parts = stem.split("-")
    index = 0
    while index < len(parts):
        segment = parts[index].strip("_ ")
        if not segment:                      # `--` 造成的空段，跟着一起剥
            index += 1
            continue
        if segment in _STAGE_WORDS or segment in _DISCIPLINE_SEGMENT_WORDS:
            index += 1
            continue
        break
    stripped = "-".join(parts[index:]).strip("-_ ")
    # 全是前缀词时不剥（否则标题会变空）
    return stripped or stem


# 多段图号（如 S-0-11-103C / S-0-31-102.01C）优先于简单图号。
# 段可以是纯数字，也可以是**字母+数字**（实测幕墙专业 `C1-H58`、`PE-A28`）。
_SEGMENT = r"(?:\d{1,4}(?:\.\d{1,2})?|[A-Za-z]{1,2}\d{1,3})[A-Za-z]?"
# 强模式：≥2 个跟随段。先试它，避免弱模式在长图名里咬到 `ST-28` 这类片段。
# 末尾禁中文：图名必含中文，`C1-H58-C1玻璃幕墙…` 里那个 `C1` 属于图名。
# 引擎会因断言失败自动回溯到更短的匹配，正好切在真图号的边界上。
_NOT_CJK = r"(?![\u4e00-\u9fff])"
_MULTI_SEGMENT_NO_RE = re.compile(
    rf"[A-Za-z]{{1,3}}\d{{0,2}}(?:[-_]{_SEGMENT}){{2,4}}{_NOT_CJK}"
)
# 弱模式：只有 1 个跟随段（`C1-H58`）。仅在强模式落空时使用。
_TWO_SEGMENT_NO_RE = re.compile(
    rf"[A-Za-z]{{1,3}}\d{{0,2}}[-_]{_SEGMENT}{_NOT_CJK}"
)
# 分离式版次：图号之后紧跟的单个大写字母段（实测装饰专业 `I-10-02-A- 2F…`）。
_DETACHED_REV_RE = re.compile(r"^[-_ ]*([A-Z])(?=[-_ ]|$)")
_DRAWING_NO_RE = re.compile(r"[A-Za-z一-龥]{1,4}[-_ ]?\d{1,4}")
# 纯序号图号：剥离「专业-阶段-」后以数字段打头（实测轨道交通基坑支护
# 图纸全是 `01`、`02`…）。**必须在剥离之后判**，否则会咬住图名里的数字。
# 尾随版次字母无分隔符也算（实测 80 张 `09A-地下连续墙预埋件详图`）。
_PLAIN_SEQUENCE_NO_RE = re.compile(r"^\d{1,4}[A-Za-z]?(?=[-_ ]|$)")
# 「字母代码.序号」形态（实测 30 张 `SPEC.001-设计说明`）。锚定行首，
# 不会咬到图名里的小数（如「1.5 米」）。
_DOTTED_CODE_NO_RE = re.compile(r"^[A-Za-z]{2,6}\.\d{1,3}(?=[-_ ]|$)")
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
    full_stem = _extract_stem(filename)
    # **专业判定用完整串**（前缀正是专业的证据），图号与标题用剥离后的串。
    stem = _strip_leading_prefix_segments(full_stem)
    no_match = (_PLAIN_SEQUENCE_NO_RE.search(stem)
                or _DOTTED_CODE_NO_RE.search(stem)
                or _MULTI_SEGMENT_NO_RE.search(stem)
                or _TWO_SEGMENT_NO_RE.search(stem)
                or _DRAWING_NO_RE.search(stem))
    drawing_no = no_match.group(0) if no_match else stem
    drawing_no_span = no_match.span() if no_match else None
    version, version_span = _detect_version(stem, drawing_no, drawing_no_span)
    discipline, discipline_confidence = _detect_discipline(full_stem)
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
    # 分离式版次：图号之后紧跟的单大写字母段，实测装饰图 `I-10-02-A- 2F…`。
    # 不处理的话版次丢失、且该段会留在标题里（`title="A- 2F平面布置图"`）。
    if drawing_no_span is not None:
        tail = stem[drawing_no_span[1]:]
        detached = _DETACHED_REV_RE.match(tail)
        if detached:
            offset = drawing_no_span[1]
            return detached.group(1), (offset + detached.start(1),
                                       offset + detached.end(1))
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
