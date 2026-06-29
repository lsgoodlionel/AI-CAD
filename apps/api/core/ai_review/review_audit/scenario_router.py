"""场景路由（V2 — 协议 4C 场景级路由 + 4D 场景排序）。

四场景：正常审图 | 图间冲突 | 施工落地 | 验收风险。

排序优先级（CONTRACT V2-4 / SKILL Scenario Priority）：
    图间冲突 > 施工落地 > 验收风险 > 正常审图

规则：
- 命中场景信号后按上述优先级取最高场景。
- risk.level == "高" 时直接切到 施工落地 或 验收风险（按文本信号；
  命中验收/合规信号优先验收风险，否则施工落地）。
- 无任何信号 → 正常审图。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CONFLICT = "图间冲突"
_LANDING = "施工落地"
_ACCEPTANCE = "验收风险"
_ROUTINE = "正常审图"

# 优先级越小越靠前
_PRIORITY: dict[str, int] = {
    _CONFLICT: 0,
    _LANDING: 1,
    _ACCEPTANCE: 2,
    _ROUTINE: 3,
}

# 场景 → 正文信号集合
_CONFLICT_SIGNALS = (
    "不一致", "矛盾", "打架", "冲突", "不符", "不匹配", "对不上", "以哪张图为准",
)
_LANDING_SIGNALS = (
    "无法施工", "无法实施", "可施工", "施工顺序", "安装顺序", "净空", "净高",
    "预留", "预埋", "套管", "标高冲突", "条件不足", "先后施工",
)
_ACCEPTANCE_SIGNALS = (
    "验收", "消防验收", "人防验收", "节能审查", "专项审查", "合规", "节能", "审查",
)

# issue_class（归类）→ 场景映射，作为正文信号的补充判定
_CLASS_TO_SCENARIO: dict[str, str] = {
    "图纸冲突": _CONFLICT,
    "接口冲突": _CONFLICT,
    "施工条件问题": _LANDING,
    "验收风险": _ACCEPTANCE,
}


def _hit(signals: tuple[str, ...], text: str) -> str | None:
    for signal in signals:
        if signal and signal in text:
            return signal
    return None


def _candidates(text: str, issue_class: list) -> dict[str, str]:
    """收集命中的候选场景 → 触发依据。"""
    found: dict[str, str] = {}

    conflict_hit = _hit(_CONFLICT_SIGNALS, text)
    if conflict_hit:
        found[_CONFLICT] = f"正文命中冲突信号「{conflict_hit}」"
    landing_hit = _hit(_LANDING_SIGNALS, text)
    if landing_hit:
        found[_LANDING] = f"正文命中施工落地信号「{landing_hit}」"
    acceptance_hit = _hit(_ACCEPTANCE_SIGNALS, text)
    if acceptance_hit:
        found[_ACCEPTANCE] = f"正文命中验收风险信号「{acceptance_hit}」"

    for cls in issue_class or []:
        scenario = _CLASS_TO_SCENARIO.get(str(cls))
        if scenario and scenario not in found:
            found[scenario] = f"问题归类「{cls}」映射到{scenario}"

    return found


def route(text: str, risk: dict, issue_class: list) -> dict:
    """返回 ``{name, priority_reason}``。

    Args:
        text: 标题 + 正文合并文本。
        risk: classifier 输出 ``{level, trigger}``。
        issue_class: classifier 输出问题归类列表。
    """
    text = text or ""
    candidates = _candidates(text, issue_class)
    high_risk = (risk or {}).get("level") == "高"

    # 高风险：直接切到施工落地 / 验收风险（按文本信号）
    if high_risk:
        if _ACCEPTANCE in candidates:
            return {
                "name": _ACCEPTANCE,
                "priority_reason": f"风险等级高且{candidates[_ACCEPTANCE]}，切换到验收风险场景",
            }
        # 图间冲突优先于施工落地解决（先消图纸不一致）
        if _CONFLICT in candidates:
            return {
                "name": _CONFLICT,
                "priority_reason": f"风险等级高，但{candidates[_CONFLICT]}，应先消解图间冲突",
            }
        landing_reason = candidates.get(_LANDING, "风险等级高，按施工落地场景处理")
        return {
            "name": _LANDING,
            "priority_reason": f"风险等级高，{landing_reason}",
        }

    if not candidates:
        return {"name": _ROUTINE, "priority_reason": "未命中冲突/施工/验收信号，按正常审图处理"}

    # 按优先级取最高场景
    best = min(candidates, key=lambda name: _PRIORITY.get(name, 99))
    return {"name": best, "priority_reason": candidates[best]}
