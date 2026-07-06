"""
图纸文件名智能解析器（蓝图 4.2）

从上传文件名中解析 {drawing_no, discipline, title, version}，
供批量上传 / ZIP 整套导入在前端元数据缺失时兜底使用。
解析不出的字段给安全默认值（discipline=general / version=A / drawing_no=文件名主干）。
"""
import re

# 专业前缀映射（按序匹配）：结施/GS→structure 建施/JS→architecture
# 水施|电施|暖施|机施/SS|DS|NS→mep 装施/ZS→decoration；无法判断→general
_DISCIPLINE_PREFIXES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("结施", "GS"), "structure"),
    (("建施", "JS"), "architecture"),
    (("水施", "电施", "暖施", "机施", "SS", "DS", "NS"), "mep"),
    (("装施", "ZS"), "decoration"),
)

_DRAWING_NO_RE = re.compile(r"[A-Za-z一-龥]{1,4}[-_ ]?\d{1,4}")
_VERSION_RE = re.compile(r"[Vv]?([A-Z])(?:版|$)")
_SEPARATORS = " -_"
DEFAULT_VERSION = "A"
DEFAULT_DISCIPLINE = "general"


def parse_drawing_filename(filename: str) -> dict:
    """解析图纸文件名，返回 {drawing_no, discipline, title, version}。

    规则（按序）：
    1. 专业前缀映射（见 _DISCIPLINE_PREFIXES）
    2. 图号：首个 `[A-Za-z一-龥]{1,4}[-_ ]?\\d{1,4}` 匹配；无匹配→文件名主干
    3. 版本：`[Vv]?([A-Z])(?:版|$)`（含 _A/_B 结尾后缀）；无匹配→'A'
    4. title = 去除图号/版本标记后的剩余主干
    """
    stem = _extract_stem(filename)
    no_match = _DRAWING_NO_RE.search(stem)
    version, version_span = _detect_version(stem)
    return {
        "drawing_no": no_match.group(0) if no_match else stem,
        "discipline": _detect_discipline(stem),
        "title": _build_title(stem, no_match.span() if no_match else None, version_span),
        "version": version,
    }


def _extract_stem(filename: str) -> str:
    """去除路径与扩展名，得到文件名主干"""
    basename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    return stem.strip()


def _detect_discipline(stem: str) -> str:
    """按专业前缀映射判断专业（字母代码大小写不敏感）"""
    upper = stem.upper()
    for prefixes, discipline in _DISCIPLINE_PREFIXES:
        if any(upper.startswith(prefix) for prefix in prefixes):
            return discipline
    return DEFAULT_DISCIPLINE


def _detect_version(stem: str) -> tuple[str, tuple[int, int] | None]:
    """提取版本号字母；无匹配返回默认版本 A"""
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
