"""Phase H5:大模型复核 —— 对低置信(conflict)构件做首轮仲裁(蓝图 §4 职责 B)。

不做几何,只对构件的**元数据上下文**(类型/轴网/来源图纸/识别途径/type_label)做语义
判断,输出 确认/否定/改类 建议,减轻人审负担(人仍是最终裁决)。

- build_review_messages / parse_review_verdict 纯函数,离线可测;
- review_component 经 ModelRouter 调 LLM,失败优雅降级(available=False),绝不中断。
"""
from __future__ import annotations

import json
from typing import Any

# 复核用引擎名(经 ModelRouter 治理;不可用时降级)
REVIEW_ENGINE = "component_review"

_VALID_VERDICTS = ("confirm", "reject", "reclass")
_VALID_TYPES = ("column", "pile", "wall", "beam", "slab", "pipe", "equipment", "door", "window")

_SYSTEM_PROMPT = (
    "你是资深结构/机电施工图审图专家。系统用确定性规则自动识别出一个三维构件,"
    "但置信度低,需要你复核。仅依据给出的元数据判断该构件的**类型**是否正确。"
    "只输出 JSON:{\"verdict\":\"confirm|reject|reclass\",\"suggested_type\":\"<类型或null>\","
    "\"reason\":\"<简短理由>\"}。confirm=类型正确;reject=不是真实构件(误检);"
    "reclass=类型错,应改为 suggested_type。类型取值:"
    + "/".join(_VALID_TYPES)
)


def build_review_messages(instance: dict) -> list[dict[str, Any]]:
    """构件元数据 → LLM messages(纯函数)。"""
    ctx = {
        "当前类型": instance.get("type"),
        "轴网定位": instance.get("grid_ref") or "无",
        "OCR类型标签": instance.get("type_label") or "无",
        "识别途径": instance.get("engines") or instance.get("source") or [],
        "来源图纸": (instance.get("source_drawings") or [])[:5],
        "观测数": instance.get("obs_count"),
        "自动置信": instance.get("confidence"),
    }
    user = "复核以下构件:\n" + json.dumps(ctx, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _extract_json(text: str) -> dict | None:
    """从 LLM 文本里抠出首个 JSON 对象(容忍 ```json 包裹/前后噪声)。"""
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


def parse_review_verdict(text: str) -> dict:
    """LLM 文本 → 规整判据 {verdict, suggested_type, reason, available}。

    无法解析/非法 verdict → available=False(视为无建议,不误导人审)。
    """
    obj = _extract_json(text)
    if obj is None:
        return {"available": False, "verdict": None, "suggested_type": None, "reason": ""}
    verdict = str(obj.get("verdict") or "").strip().lower()
    if verdict not in _VALID_VERDICTS:
        return {"available": False, "verdict": None, "suggested_type": None, "reason": ""}
    suggested = obj.get("suggested_type")
    suggested = str(suggested).strip().lower() if suggested else None
    if verdict == "reclass" and suggested not in _VALID_TYPES:
        # 改类却没给合法新类型 → 建议无效
        return {"available": False, "verdict": None, "suggested_type": None, "reason": ""}
    return {
        "available": True,
        "verdict": verdict,
        "suggested_type": suggested if verdict == "reclass" else None,
        "reason": str(obj.get("reason") or "").strip()[:200],
    }


async def review_component(instance: dict, router: Any) -> dict:
    """经 LLM 复核单个构件。router 为 None 或调用失败 → available=False(降级)。"""
    if router is None:
        return {"available": False, "verdict": None, "suggested_type": None, "reason": ""}
    try:
        messages = build_review_messages(instance)
        response = await router.route(REVIEW_ENGINE, messages)
        return parse_review_verdict(getattr(response, "content", "") or "")
    except Exception:  # noqa: BLE001 — LLM 失败降级,绝不中断
        return {"available": False, "verdict": None, "suggested_type": None, "reason": ""}
