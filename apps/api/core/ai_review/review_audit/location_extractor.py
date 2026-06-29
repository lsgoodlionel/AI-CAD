"""定位信息抽取：跑 location_patterns 正则，输出五类定位去重列表。

固定执行协议第2步：抽图号、层位、轴线、节点号/系统号、房间/设备名称。
"""
from __future__ import annotations

import logging
import re

from .protocol_loader import load_location_patterns

logger = logging.getLogger(__name__)

# 输出五个键固定存在，保证下游消费稳定
_LOCATION_KINDS = ("drawings", "levels", "axes", "nodes_or_systems", "spaces")


def _dedupe(items: list[str]) -> list[str]:
    """保序去重，剔除空白。"""
    seen: set[str] = set()
    result: list[str] = []
    for raw in items:
        value = raw.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def extract(text: str) -> dict:
    """返回 ``{drawings, levels, axes, nodes_or_systems, spaces}``（各为去重列表）。"""
    result: dict[str, list[str]] = {kind: [] for kind in _LOCATION_KINDS}
    if not text:
        return result

    patterns = load_location_patterns()
    for kind in _LOCATION_KINDS:
        matches: list[str] = []
        for raw_pattern in patterns.get(kind, []):
            try:
                for match in re.finditer(raw_pattern, text):
                    matches.append(match.group(0))
            except re.error as exc:
                logger.warning("定位正则编译失败 kind=%s pattern=%r: %s", kind, raw_pattern, exc)
        result[kind] = _dedupe(matches)
    return result
