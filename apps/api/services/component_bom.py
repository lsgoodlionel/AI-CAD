"""Phase H5 职责 A:构件表 → BOM(设计数量)→ 驱动数量对齐(蓝图 §4 A)。

- reconcile_from_counts:实体计数 vs BOM,纯函数,离线可测(数量对齐核心)。
- build_bom_messages / parse_bom:LLM 从构件表文本抽 BOM,纯函数可测;
- extract_bom:经 ModelRouter 调 LLM,失败降级(available=False),绝不中断。

BOM 形如 {"column": 480, "pile": 96, ...}(按实体 type 归并的设计数量)。
"""
from __future__ import annotations

import json
from typing import Any

REVIEW_ENGINE = "component_review"

_VALID_TYPES = ("column", "pile", "wall", "beam", "slab", "pipe", "equipment", "door", "window")

_SYSTEM_PROMPT = (
    "你是施工图算量专家。给你一张构件表(或其文字),请统计各类构件的**设计总数量**,"
    "按标准类型归并。只输出 JSON:{\"<类型>\": <数量整数>, ...}。"
    "类型取值:" + "/".join(_VALID_TYPES) + "。无法确定的忽略,不要编造。"
)


def reconcile_from_counts(by_type: dict[str, int], bom: dict[str, int]) -> dict[str, dict]:
    """实体计数(by_type)vs 设计 BOM,返回每型 {expected, actual, diff}。

    diff>0 = 少识别(漏);diff<0 = 多识别(重复/误检)。BOM 未列的型不报。
    """
    report: dict[str, dict] = {}
    for ctype, expected in bom.items():
        actual = int(by_type.get(ctype) or 0)
        report[ctype] = {
            "expected": int(expected),
            "actual": actual,
            "diff": int(expected) - actual,
        }
    return report


def build_bom_messages(schedule_text: str) -> list[dict[str, Any]]:
    """构件表文本 → LLM messages(纯函数)。"""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "构件表:\n" + (schedule_text or "")[:8000]},
    ]


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


def parse_bom(text: str) -> dict:
    """LLM 文本 → {available, bom}。仅保留合法类型 + 正整数,防脏值污染对齐。"""
    obj = _extract_json(text)
    if obj is None:
        return {"available": False, "bom": {}}
    bom: dict[str, int] = {}
    for key, value in obj.items():
        ctype = str(key).strip().lower()
        if ctype not in _VALID_TYPES:
            continue
        try:
            n = int(value)
        except (ValueError, TypeError):
            continue
        if n > 0:
            bom[ctype] = n
    return {"available": bool(bom), "bom": bom}


async def extract_bom(schedule_text: str, router: Any) -> dict:
    """经 LLM 从构件表抽 BOM。router 为 None / 失败 → available=False(降级)。"""
    if router is None or not (schedule_text or "").strip():
        return {"available": False, "bom": {}}
    try:
        messages = build_bom_messages(schedule_text)
        response = await router.route(REVIEW_ENGINE, messages)
        return parse_bom(getattr(response, "content", "") or "")
    except Exception:  # noqa: BLE001 — LLM 失败降级
        return {"available": False, "bom": {}}
