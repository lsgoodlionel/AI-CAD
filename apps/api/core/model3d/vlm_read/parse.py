"""VLM 原始回答文本 → 结构化候选（纯函数，可离线测，无网络依赖）。

调用方用固定提示词（见 ``ollama_vlm._PROMPT``）要求模型按
「专业：/标高：/构件：」三行结构化作答，但对话式模型仍可能夹带说明文字或
偏离格式；解析器优先匹配结构化行（高置信），命中不到时退化为全文关键词/
正则兜底扫描（低置信）。任何一步都可能解析不出内容——留空即可，绝不编造。
"""
from __future__ import annotations

import re

from .types import ComponentCandidate, DisciplineCandidate, ElevationCandidate, VlmReadResult

# 结构化行是模型对提示词的显式作答，置信度高于全文兜底扫描（后者只是猜测）
_CONF_STRUCTURED = 0.85
_CONF_FALLBACK = 0.55

_DISCIPLINES = ("建筑", "结构", "给排水", "暖通", "电气", "道路", "景观")

_RE_DISCIPLINE_LINE = re.compile(r"专业[：:]\s*([^\n]+)")
_RE_ELEVATION_LINE = re.compile(r"标高[：:]\s*([^\n]+)")
_RE_COMPONENT_LINE = re.compile(r"构件[：:]\s*([^\n]+)")

# 标高数值：±0.000 / +3.600 / -1.500 / 15.00（工程图惯例，1~3 位整数 + 2~3 位小数，可带 ± 前缀）
_RE_ELEVATION_VALUE = re.compile(r"[±+\-]?\d{1,3}\.\d{2,3}")
# 合理标高范围（米），过滤模型误读/幻觉出的荒谬数值——宁可漏读不可虚高
_ELEVATION_MIN_M = -30.0
_ELEVATION_MAX_M = 300.0

# 构件词表按长度降序匹配，避免"基础底板"被"基础"截断丢信息
_COMPONENT_VOCAB = (
    "基础底板", "剪力墙", "构造柱", "楼梯", "雨棚", "女儿墙", "圈梁",
    "过梁", "阳台", "飘窗", "承台", "风管", "桥架", "阀门",
    "梁", "板", "柱", "基础", "墙", "桩", "管道", "设备",
)


def _parse_elevation_value(text: str) -> float | None:
    """标高文本→米。±0.000→0.0、+3.600→3.6、-1.500→-1.5；超出合理范围返回 None。"""
    cleaned = text.replace("±", "").replace("＋", "+").replace("－", "-")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if not (_ELEVATION_MIN_M <= value <= _ELEVATION_MAX_M):
        return None
    return value


def parse_discipline(raw_text: str) -> DisciplineCandidate | None:
    """从 VLM 回答中判专业。优先「专业：」结构化行，否则退化为全文关键词扫描。

    模型显式声明"无法判断/unknown"时不猜——按铁律，不确定就留空。
    """
    line_match = _RE_DISCIPLINE_LINE.search(raw_text)
    if line_match:
        line_text = line_match.group(1)
        for discipline in _DISCIPLINES:
            if discipline in line_text:
                return DisciplineCandidate(
                    value=discipline, confidence=_CONF_STRUCTURED, evidence=line_match.group(0).strip()
                )
        return None  # 结构化行存在但未命中已知专业（如显式"unknown"）——不猜

    for discipline in _DISCIPLINES:
        if discipline in raw_text:
            idx = raw_text.index(discipline)
            evidence = raw_text[max(0, idx - 5) : idx + 10].strip()
            return DisciplineCandidate(value=discipline, confidence=_CONF_FALLBACK, evidence=evidence)
    return None


def parse_elevations(raw_text: str) -> tuple[ElevationCandidate, ...]:
    """从 VLM 回答中提取标高候选（米）。按「标高：」行优先，去重、按数值升序。"""
    line_match = _RE_ELEVATION_LINE.search(raw_text)
    segment = line_match.group(1) if line_match else raw_text
    confidence = _CONF_STRUCTURED if line_match else _CONF_FALLBACK

    seen: set[float] = set()
    out: list[ElevationCandidate] = []
    for match in _RE_ELEVATION_VALUE.finditer(segment):
        value = _parse_elevation_value(match.group())
        if value is None or value in seen:
            continue
        seen.add(value)
        out.append(ElevationCandidate(value_m=value, confidence=confidence, evidence=match.group()))
    out.sort(key=lambda c: c.value_m)
    return tuple(out)


def parse_components(raw_text: str) -> tuple[ComponentCandidate, ...]:
    """从 VLM 回答中提取构件类别候选（去重，不含计数/坐标/尺寸）。

    按字符区间去重而非整词去重：单字词（如"板"）本身可能是复合词（如
    "基础底板"）的子串，仅当其命中位置落在已被更长词覆盖的区间内才丢弃该次
    出现；同一单字若在文本别处独立出现（不在覆盖区间内），仍算有效候选。
    """
    line_match = _RE_COMPONENT_LINE.search(raw_text)
    segment = line_match.group(1) if line_match else raw_text
    confidence = _CONF_STRUCTURED if line_match else _CONF_FALLBACK

    claimed: list[tuple[int, int]] = []
    seen_labels: set[str] = set()
    out: list[ComponentCandidate] = []
    for label in _COMPONENT_VOCAB:
        found_valid_occurrence = False
        for match in re.finditer(re.escape(label), segment):
            start, end = match.span()
            if any(start < c_end and end > c_start for c_start, c_end in claimed):
                continue  # 该出现已被更长的词覆盖（如"基础底板"内的"板"字），跳过
            claimed.append((start, end))
            found_valid_occurrence = True
        if found_valid_occurrence and label not in seen_labels:
            seen_labels.add(label)
            out.append(ComponentCandidate(label=label, confidence=confidence, evidence=label))
    return tuple(out)


def parse_vlm_text(raw_text: str, *, model: str = "", backend: str = "qwen3.5-vision") -> VlmReadResult:
    """VLM 原始回答文本 → 结构化 ``VlmReadResult``。空文本降级为 backend=none。"""
    if not raw_text or not raw_text.strip():
        return VlmReadResult(raw_text=raw_text, backend="none", model=model, warnings=("VLM 返回空文本",))
    return VlmReadResult(
        discipline=parse_discipline(raw_text),
        elevations=parse_elevations(raw_text),
        components=parse_components(raw_text),
        raw_text=raw_text,
        backend=backend,
        model=model,
    )
