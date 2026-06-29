"""对象识别（V2 — 协议 4B 对象级路由）。

识别问题指向的对象（构件/部位/系统/节点）及其级别（部位级|系统级|节点级）。

判定优先级（CONTRACT V2-4）：
1. 正文显式命中 disciplines.yaml 该专业 objects → basis="显式命名"。
2. 否则按该专业高频对象（objects 首项）+ 首要 concern 推定
   → basis="推定（依据:专业高频对象+concern）"。
3. 知识缺失（该专业无 objects）→ 退回 concern 级，object 留空，basis="证据不足"。
"""
from __future__ import annotations

import logging

from .protocol_loader import load_disciplines

logger = logging.getLogger(__name__)

_BASIS_EXPLICIT = "显式命名"
_BASIS_INFERRED = "推定（依据:专业高频对象+concern）"
_BASIS_WEAK = "证据不足"


def _discipline_objects(discipline_code: str) -> list[dict]:
    """返回该专业 objects（``[{name, level}, ...]``），无知识返回空列表。"""
    disc = load_disciplines().get(discipline_code, {})
    objects = disc.get("objects", []) or []
    return [obj for obj in objects if isinstance(obj, dict) and obj.get("name")]


def _first_concern_label(concerns: list[dict]) -> str:
    for concern in concerns or []:
        label = str((concern or {}).get("label", "")).strip()
        if label:
            return label
    return ""


def identify(discipline_code: str, concerns: list[dict], text: str) -> dict:
    """返回 ``{level, object, basis}``。

    Args:
        discipline_code: 已判定的细分专业代码（如 JG）。
        concerns: concern_extractor 输出 ``[{label, reason}, ...]``。
        text: 标题 + 正文合并文本。
    """
    text = text or ""
    objects = _discipline_objects(discipline_code)

    # 1. 正文显式命中（取最长名优先，避免短名误命中）
    explicit = [obj for obj in objects if str(obj.get("name", "")) and str(obj["name"]) in text]
    if explicit:
        explicit.sort(key=lambda o: len(str(o.get("name", ""))), reverse=True)
        hit = explicit[0]
        return {
            "level": str(hit.get("level", "")),
            "object": str(hit.get("name", "")),
            "basis": _BASIS_EXPLICIT,
        }

    # 2. 该专业高频对象（objects 首项）+ 首要 concern 推定
    if objects:
        head = objects[0]
        return {
            "level": str(head.get("level", "")),
            "object": str(head.get("name", "")),
            "basis": _BASIS_INFERRED,
        }

    # 3. 知识缺失 → 退回 concern 级，object 不杜撰
    label = _first_concern_label(concerns)
    if label:
        logger.debug("[object_identifier] %s 无对象知识，降级到 concern 级: %s", discipline_code, label)
    return {"level": "", "object": "", "basis": _BASIS_WEAK}
