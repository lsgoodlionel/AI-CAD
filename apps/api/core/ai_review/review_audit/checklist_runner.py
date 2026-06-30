"""SOP 逐项清单核查（接入 ``05_专业审图清单SOP`` 蒸馏资产）。

给定专业 + 正文 + concerns + 定位 + 场景 + 问题归类 + 风险，逐项核查本专业 SOP 清单，
输出：受保护实施结果（protected_result）、未来影响阶段（future_impact）、
清单覆盖率与高价值未覆盖项（coverage）。

设计约束：
- 纯模板确定性逻辑，无 LLM、无 db 也可运行；
- ``review_checklists.yaml`` 缺失/无 pyyaml 时降级为空结构（绝不抛异常）。
"""
from __future__ import annotations

import logging

from .protocol_loader import load_review_checklists

logger = logging.getLogger(__name__)

# ── 问题归类 → 未来影响阶段（成本由高到低）────────────────────────
_STAGE_BY_CLASS: dict[str, str] = {
    "表达遗漏": "设计深化",
    "图纸冲突": "提资/下料",
    "接口冲突": "预留预埋",
    "施工条件问题": "安装穿插",
    "验收风险": "调试验收",
}
# 实施成本权重（命中多个归类时取最高代价阶段）
_STAGE_COST: dict[str, int] = {
    "预留预埋": 5,
    "安装穿插": 4,
    "调试验收": 3,
    "提资/下料": 2,
    "设计深化": 1,
    "运营维护": 0,
}
_DEFAULT_STAGE = "设计深化"


# ── 清单项类型判定 ────────────────────────────────────────────────
def _item_kind(title: str) -> str:
    if "专业归属" in title:
        return "meta_discipline"
    if "定位信息" in title:
        return "meta_location"
    if "联合检查" in title or "接口" in title:
        return "interface"
    if "可落地" in title:
        return "constructability"
    if "风险分级" in title or "升级" in title:
        return "risk"
    return "concern"


def _concern_token(title: str) -> str:
    """从 concern 类检查项标题提取核心词，如 ``标高 检查`` → ``标高``。"""
    return title.replace("联合检查", "").replace("检查", "").replace(" ", "").strip()


def _evaluate_item(
    item: dict,
    *,
    text: str,
    concern_labels: set[str],
    has_location: bool,
    issue_class: list[str],
    risk_level: str,
) -> tuple[bool, bool]:
    """返回 ``(命中, 覆盖)``。命中=该检查项适用于本记录；覆盖=记录已提供其所需证据。"""
    title = str(item.get("检查项", ""))
    kind = _item_kind(title)

    if kind == "meta_discipline":
        return True, True
    if kind == "meta_location":
        return True, has_location
    if kind == "interface":
        return True, "接口冲突" not in issue_class
    if kind == "constructability":
        return True, "施工条件问题" not in issue_class
    if kind == "risk":
        return True, risk_level != "高"

    # concern 类：命中=核心词出现在正文或已抽取 concern；覆盖=命中且有定位证据
    token = _concern_token(title)
    applicable = bool(token) and (token in text or token in concern_labels)
    return applicable, (applicable and has_location)


def _future_impact(issue_class: list[str], consequence_chain: list[str]) -> dict:
    """由问题归类推导未来影响阶段，并取后果链对应文本作为 effect。"""
    stages = [_STAGE_BY_CLASS[c] for c in issue_class if c in _STAGE_BY_CLASS]
    stage = max(stages, key=lambda s: _STAGE_COST.get(s, 0)) if stages else _DEFAULT_STAGE

    cost = _STAGE_COST.get(stage, 1)
    effect = ""
    if consequence_chain:
        if cost >= 4 and len(consequence_chain) > 1:
            effect = consequence_chain[1]
        elif cost == 3:
            effect = consequence_chain[2] if len(consequence_chain) > 2 else consequence_chain[-1]
        else:
            effect = consequence_chain[0]
    return {"stage": stage, "effect": effect}


def run(
    discipline_code: str,
    text: str,
    concerns: list[dict],
    location: dict,
    scenario: dict,
    issue_class: list[str],
    risk: dict,
) -> dict:
    """逐项核查本专业 SOP 清单，返回 SOP 增强结构。yaml 缺失时返回 ``{}``。"""
    cl = load_review_checklists().get(discipline_code, {})
    if not cl:
        return {}

    text = text or ""
    issue_class = issue_class or []
    risk = risk or {}
    scenario = scenario or {}
    concern_labels = {str((c or {}).get("label", "")) for c in (concerns or [])}
    concern_labels.discard("")
    has_location = any((location or {}).values())
    risk_level = str(risk.get("level", ""))

    items_out: list[dict] = []
    uncovered: list[dict] = []
    checked = covered = 0
    for item in cl.get("checklist", []) or []:
        hit, cover = _evaluate_item(
            item,
            text=text,
            concern_labels=concern_labels,
            has_location=has_location,
            issue_class=issue_class,
            risk_level=risk_level,
        )
        record = {
            "检查项": str(item.get("检查项", "")),
            "命中": hit,
            "覆盖": cover,
            "升级": bool(item.get("升级", False)),
            "必问问题": str(item.get("必问问题", "")),
            "输出口径": str(item.get("输出口径", "")),
        }
        items_out.append(record)
        if hit:
            checked += 1
            if cover:
                covered += 1
            else:
                uncovered.append(
                    {
                        "检查项": record["检查项"],
                        "必问问题": record["必问问题"],
                        "输出口径": record["输出口径"],
                        "升级": record["升级"],
                    }
                )

    ratio = round(covered / checked, 2) if checked else 0.0
    why_now = (
        f"按「{scenario.get('name', '正常审图')}」场景优先；{risk.get('trigger', '')}"
    ).strip("；")

    return {
        "protected_result": str(cl.get("protected_result", "")),
        "why_now": why_now,
        "future_impact": _future_impact(issue_class, cl.get("consequence_chain", []) or []),
        "coverage": {
            "ratio": ratio,
            "checked": checked,
            "covered": covered,
            "items": items_out,
            "uncovered": uncovered,
        },
    }
