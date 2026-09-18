"""规则注册与执行 —— 一个模型跑一遍所有规则，产出报告。

**三种结局分开记**：出结论（`findings`）、跑不了（`skipped`）、代码炸了
（`errored`）。第三种此前在本仓库被宽泛 `except` 吞过两次，整条通道
「从未生效」而日志只有 info 一行；这里把它单列，并且**不吞堆栈摘要**。
"""
from __future__ import annotations

import logging

from core.model3d.plausibility.types import (
    Finding, Report, Rule, RuleNotApplicable, sort_findings,
)

logger = logging.getLogger(__name__)

#: 规则表由各族在导入时注册（`register(rule)`）。顺序按 id，不按导入顺序 ——
#: 报告不能随 import 次序漂移。
_RULES: dict[str, Rule] = {}


def register(rule: Rule) -> Rule:
    if rule.id in _RULES:
        raise ValueError(f"规则 id 重复：{rule.id}")
    _RULES[rule.id] = rule
    return rule


def all_rules() -> tuple[Rule, ...]:
    return tuple(_RULES[key] for key in sorted(_RULES))


#: 规则族模块名。加载失败的记在 `_LOAD_ERRORS`，由 `run` 带进报告。
RULE_MODULES = ("rules_geometry", "rules_dimension", "rules_support",
                "rules_statics", "rules_quantity")

_LOAD_ERRORS: dict[str, str] = {}


def load_rules() -> tuple[Rule, ...]:
    """逐个导入规则族（副作用是注册），返回全表。

    放在函数里而不是模块顶层 import：规则族反过来要 import 本模块的
    `register`，顶层互导会成环。

    **一族导入失败不能拖垮整轮，但必须可见**：记进 `_LOAD_ERRORS`，
    `run` 会把它并进报告的 `errored`。少跑了一族却报告「全部通过」，
    正是本仓库付过代价的那种静默失效。
    """
    import importlib

    _LOAD_ERRORS.clear()
    for name in RULE_MODULES:
        try:
            importlib.import_module(f"core.model3d.plausibility.{name}")
        except Exception as exc:  # noqa: BLE001
            _LOAD_ERRORS[name] = f"{type(exc).__name__}: {exc}"
            logger.error("合理性规则族 %s 导入失败：%s", name, exc)
    return all_rules()


def run(model, *, rules: tuple[Rule, ...] | None = None) -> Report:
    """跑一遍规则。`model` 是 `plausibility.model.PlausibilityModel`。"""
    findings: list[Finding] = []
    checked: dict[str, int] = {}
    skipped: dict[str, str] = {}
    errored: dict[str, str] = {}
    if rules is None:
        rules = load_rules()
        errored.update({f"<{name} 未加载>": reason for name, reason in _LOAD_ERRORS.items()})
    for rule in rules:
        try:
            produced = list(rule.check(model))
        except RuleNotApplicable as exc:
            skipped[rule.id] = str(exc) or "未说明原因"
            continue
        except Exception as exc:  # noqa: BLE001 - 规则有 bug 不该拖垮整轮，但必须可见
            errored[rule.id] = f"{type(exc).__name__}: {exc}"
            logger.exception("合理性规则 %s 抛异常", rule.id)
            continue
        findings.extend(produced)
        checked[rule.id] = _denominator(model, rule)
    return Report(findings=sort_findings(findings), checked=checked,
                  skipped=skipped, errored=errored)


def _denominator(model, rule: Rule) -> int:
    """这条规则实际面对多少个对象 —— 没有分母就算不出发生率。"""
    if rule.scope == "model":
        return 1
    if rule.scope == "floor":
        return sum(1 for _ in model.floors())
    return model.count()
