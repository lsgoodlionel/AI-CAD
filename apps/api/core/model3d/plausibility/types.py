"""现实合理性分析的公共契约 —— 规则、结论、报告。

**这一层回答的问题**：模型里这根柱、这块板、这一层、这栋楼，
**在现实世界里可能存在吗**。它不问「识别得准不准」（那是金标准的事），
只问「按数学、物理、规范的下限，它能不能成立」。

两者互补：金标准量的是「与图纸比对得对不对」，要人判读、有滞后；
合理性分析是**纯计算、零人工、可在每次建模后自动跑**的一道闸。
实测已知的几类错误正好落在它的射程内 —— 自交轮廓让 31% 的柱混凝土量算成
零、兜底板把图框当成楼板导致 519,684 m³ 方量、总图与分图重复导致柱被算几遍。

**三档严重度，按「凭什么否定它」分**，不按「看起来多离谱」分：

| 档 | 含义 | 依据类型 |
|---|---|---|
| `impossible` | 数学或物理上不可能 | 定理、守恒律（自交多边形、负体积、悬浮构件、层高 ≤ 0） |
| `implausible` | 违反规范强制下限或工程量级 | 国标条款原文、量纲分析 |
| `suspect` | 统计离群 | 本模型内部分布（不是外部标准） |

**降级必须可见**：规则跑不了（缺标高、缺截面、缺荷载）时要进 `skipped`
并写明缺什么，**不能当成通过**。这条是本仓库反复付过代价的纪律 ——
静默降级会让一整条通道「从未生效」而无人察觉。
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

#: 严重度。顺序即排序优先级。
SEVERITIES = ("impossible", "implausible", "suspect")

#: 规则作用域：逐构件 / 逐层 / 整模型。决定引擎怎么喂数据，也决定
#: `target` 指向什么。
SCOPES = ("element", "floor", "model")


@dataclass(frozen=True)
class Finding:
    """一条不合理结论。

    `evidence` 必须装**数字**（实测值、阈值、代入公式的中间量），不是形容词 ——
    「柱太小」无法复核，`{"b_mm": 180, "limit_mm": 300}` 可以。
    `basis` 写依据出处（条款号 / 定理名 / 量纲式），空字符串不允许。
    """
    rule: str
    severity: str
    kind: str                      # columns / walls / beams / slabs / pipes / equipment / floor / model
    target: str                    # 构件 uid、楼层 key，或 "model"
    detail: str
    evidence: dict = field(default_factory=dict)
    basis: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"未知严重度 {self.severity!r}")
        if not self.basis:
            raise ValueError(f"规则 {self.rule} 的结论没写依据 —— 无依据的否定不可复核")


@dataclass(frozen=True)
class Rule:
    """一条可执行的合理性规则。

    `check(model) -> Iterable[Finding]`：拿到整个模型，自己决定遍历粒度。
    跑不了时**不要返回空**，抛 `RuleNotApplicable` 说明缺什么。
    """
    id: str
    title: str
    scope: str
    severity: str
    basis: str
    check: Callable[..., Iterable[Finding]]

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise ValueError(f"未知作用域 {self.scope!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"未知严重度 {self.severity!r}")


class RuleNotApplicable(Exception):
    """规则在这个模型上跑不了（缺数据），**不是通过**。

    抛它而不是返回空列表：空列表的意思是「查过了，没问题」，
    而缺数据时我们根本没查。两者混在一起，报告就会把「没量」说成「合格」。
    """


@dataclass(frozen=True)
class Report:
    """一次合理性分析的完整结果。"""
    findings: tuple[Finding, ...] = ()
    #: 规则 id → 这条规则实际检查了多少个对象（分母，用来算发生率）
    checked: dict[str, int] = field(default_factory=dict)
    #: 规则 id → 跑不了的原因。**报告里必须能看见它**
    skipped: dict[str, str] = field(default_factory=dict)
    #: 规则 id → 抛异常的原因（代码 bug，与 skipped 分开记，不混为「缺数据」）
    errored: dict[str, str] = field(default_factory=dict)

    def by_severity(self, severity: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == severity)

    @property
    def counts(self) -> dict[str, int]:
        return {s: len(self.by_severity(s)) for s in SEVERITIES}

    def as_dict(self) -> dict:
        return {
            "counts": self.counts,
            "checked": dict(self.checked),
            "skipped": dict(self.skipped),
            "errored": dict(self.errored),
            "findings": [
                {"rule": f.rule, "severity": f.severity, "kind": f.kind,
                 "target": f.target, "detail": f.detail,
                 "evidence": f.evidence, "basis": f.basis}
                for f in self.findings
            ],
        }


def sort_findings(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """按严重度、规则 id、目标排序 —— 报告的顺序不能随字典遍历漂移。"""
    order = {s: i for i, s in enumerate(SEVERITIES)}
    return tuple(sorted(findings, key=lambda f: (order[f.severity], f.rule, f.target)))


def require(values: Sequence, what: str) -> None:
    """常用守卫：没有数据就抛 `RuleNotApplicable`，而不是悄悄返回空。"""
    if not values:
        raise RuleNotApplicable(what)
