"""问题归类 + 风险定级 + 接口复核。

固定执行协议第6/7步 + 风险升级规则（第6节）。
- 归类 ∈ 表达遗漏 | 图纸冲突 | 接口冲突 | 施工条件问题 | 验收风险
- 风险按 disciplines.yaml 的 risk_triggers + 通用升级规则
- 接口取该专业 default_interfaces
"""
from __future__ import annotations

import logging

from .protocol_loader import load_disciplines

logger = logging.getLogger(__name__)

# ── 归类关键词（概念→正文信号词）────────────────────────────────
_CLASS_KEYWORDS: dict[str, tuple[str, ...]] = {
    "图纸冲突": ("不一致", "矛盾", "打架", "冲突", "不符", "不匹配", "对不上"),
    "表达遗漏": ("不完整", "缺失", "未明确", "未表达", "遗漏", "无法明确", "未注明", "缺少"),
    "接口冲突": ("接口", "预留", "预埋", "套管", "穿墙", "联查", "跨专业"),
    "施工条件问题": ("无法施工", "可施工", "施工顺序", "安装顺序", "净空", "净高", "无法实施", "条件不足"),
    "验收风险": ("验收", "消防验收", "人防验收", "节能审查", "专项审查", "合规"),
}

# ── 通用风险升级信号（第6节）──────────────────────────────────
_HIGH_RISK_SIGNALS: tuple[str, ...] = (
    "结构安全", "基坑", "围护", "桩基", "钢结构节点",
    "消防", "联动", "主干", "主机房", "机电主干",
    "标高冲突", "净空", "预留预埋", "安装顺序",
    "专项审查", "消防验收", "人防验收", "节能审查",
    "返工",
)
_MEDIUM_RISK_SIGNALS: tuple[str, ...] = (
    "标高", "净高", "做法", "节点", "系统", "接口", "预留",
)

_RISK_HIGH = "高"
_RISK_MEDIUM = "中"
_RISK_LOW = "低"


def _classify_issue(text: str) -> list[str]:
    """返回命中的问题归类（保证至少 1 项）。"""
    classes: list[str] = [
        cls for cls, words in _CLASS_KEYWORDS.items() if any(w in text for w in words)
    ]
    if not classes:
        classes.append("表达遗漏")  # 协议默认：未闭合即表达遗漏
    return classes


def _assess_risk(discipline_code: str, text: str, multi_discipline: bool) -> dict:
    """返回 ``{level, trigger}``。"""
    disc = load_disciplines().get(discipline_code, {})
    triggers = [str(t) for t in disc.get("risk_triggers", []) or []]

    # 专业级 risk_triggers：触发词命中即高风险
    for trigger in triggers:
        # trigger 是整句描述，取其中关键短语做包含判断
        if trigger and any(token and token in text for token in _tokenize_trigger(trigger)):
            return {"level": _RISK_HIGH, "trigger": trigger}

    # 多专业互相矛盾 + 责任边界不清 → 高
    if multi_discipline:
        return {"level": _RISK_HIGH, "trigger": "两个及以上专业图纸互相矛盾，责任边界不清"}

    # 通用高风险信号
    for signal in _HIGH_RISK_SIGNALS:
        if signal in text:
            return {"level": _RISK_HIGH, "trigger": f"涉及{signal}，按风险升级规则定为高"}

    # 中风险信号
    for signal in _MEDIUM_RISK_SIGNALS:
        if signal in text:
            return {"level": _RISK_MEDIUM, "trigger": f"涉及{signal}，存在施工依据不明风险"}

    return {"level": _RISK_LOW, "trigger": "未命中升级信号，按一般问题处理"}


def _tokenize_trigger(trigger: str) -> list[str]:
    """把整句 risk_trigger 切成可包含匹配的关键短语（按标点/连接词粗切）。"""
    seps = ["、", "，", ",", "；", ";", "和", "或", "及"]
    parts = [trigger]
    for sep in seps:
        nxt: list[str] = []
        for part in parts:
            nxt.extend(part.split(sep))
        parts = nxt
    return [p.strip() for p in parts if len(p.strip()) >= 2]


def _assess_interface(discipline_code: str, name: str) -> dict:
    """返回 ``{primary, related, reason}``。"""
    disc = load_disciplines().get(discipline_code, {})
    related = [str(i) for i in disc.get("default_interfaces", []) or []]
    primary = name or str(disc.get("name_cn", "")) or discipline_code
    reason = (
        f"{primary}为主责专业，按该专业默认接口联查 {('、'.join(related)) or '（无预设接口）'}"
    )
    return {"primary": primary, "related": related, "reason": reason}


def classify(
    discipline_code: str,
    concerns: list[dict],
    text: str,
    location: dict,
) -> dict:
    """返回 ``{issue_class, risk, interface}``。"""
    text = text or ""
    disc = load_disciplines().get(discipline_code, {})
    name = str(disc.get("name_cn", ""))

    issue_class = _classify_issue(text)

    # 多专业判定：接口冲突归类 + 命中多个接口专业名
    related = [str(i) for i in disc.get("default_interfaces", []) or []]
    hit_interfaces = [i for i in related if i and i in text]
    multi_discipline = "接口冲突" in issue_class and len(hit_interfaces) >= 2

    risk = _assess_risk(discipline_code, text, multi_discipline)
    interface = _assess_interface(discipline_code, name)

    return {"issue_class": issue_class, "risk": risk, "interface": interface}
