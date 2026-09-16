"""合理性引擎 —— 把「这些构件在现实里立不立得住」接进 AI 审图。

**与另外几个引擎的分工**：规则引擎查图面文字与元数据，KG/RAG 查规范条文，
视觉引擎认图元；本引擎拿**已经识别出来的构件**，只问一件事 ——
按数学、物理、规范下限，它们可能存在吗。所以它必须排在视觉引擎之后。

**单张图的先天缺口**：一张平面图没有标高、没有层高、没有上下层关系。
凡是要跨层的规则（支承、竖向连续、荷载传递）在这里必然 `RuleNotApplicable`，
这是正确行为 —— 但**必须看得见**：`skipped`/`errored` 如实带出来，
不能当成「查过了，没问题」。整楼场景要跑完整规则，走
`plausibility.model.from_scene(scene)`，不走本模块的 `single_drawing_model`。

**这一层不碰 DB**：构件由 `elements_provider` 注入（默认读 `ctx.ocr_metadata`），
因此引擎可以离线全测，也不会把 `services/` 的依赖带进 `core/ai_review`。
纯函数（`single_drawing_model`/`aggregate`/`report_to_issues`）与 IO
（`PlausibilityEngine.analyze`）分开，规则不在 import 时跑。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from core.model3d.plausibility import registry
from core.model3d.plausibility.model import (
    ELEMENT_KINDS, PlausibilityModel, from_scene,
)
from core.model3d.plausibility.types import Finding, Report

from .base import AIIssue, BaseEngine, DrawingContext, IssueSeverity

logger = logging.getLogger(__name__)

ENGINE_NAME = "plausibility"

#: 单图模型里那一层的固定 key。用常量而不是 "1F" 之类的假楼层名 ——
#: 单张图**不知道自己是第几层**，起个楼层名等于凭空造数据。
SINGLE_FLOOR_KEY = "single"

#: 默认从 `ctx.ocr_metadata` 的这个键取识别结果（`FloorElements.as_dict()` 的形状）。
#: 视觉引擎先跑、填 `ocr_metadata`，本引擎再读 —— 与既有 `extracted_text`
#: 的传递方式一致，不新开通道。
ELEMENTS_METADATA_KEY = "floor_elements"

#: 三档严重度 → 审图问题严重度。**按「凭什么否定它」映射，不按「看起来多离谱」**：
#: - `impossible` 数学/物理上不成立（自交环、负体积、层高 ≤ 0）。这类构件的
#:   工程量一定是错的（实测 31% 的柱因轮廓自交把混凝土量算成零），必须处理；
#: - `implausible` 违反规范强制下限或工程量级。可能是识别错、也可能真违规，
#:   两者都要人看，但不到「必然错」；
#: - `suspect` 只是本模型内部的统计离群，没有外部标准支撑，误报率天然高，
#:   压到最低档以免淹没前两档。
#: INFO 档**留给引擎自身的降级说明**，不给任何 finding 用 ——
#: 这样界面上「查出问题」与「根本没查」区分得开。
SEVERITY_MAP: dict[str, IssueSeverity] = {
    "impossible":  IssueSeverity.CRITICAL,
    "implausible": IssueSeverity.MAJOR,
    "suspect":     IssueSeverity.MINOR,
}

SEVERITY_LABELS = {"impossible": "不可能", "implausible": "不合常理", "suspect": "可疑"}

#: 一条规则命中再多次，也只列这么多个样例。
#: 为什么是 5：一张图上一条规则可命中数百次（几百根柱同一种自交），
#: 全列会把问题单淹没；而复核者只需要几个样例就能判断「是不是同一类错误」，
#: 剩下的靠命中数与分母。总条数因此被规则数（数十条）自然封顶，无需再设总上限。
SAMPLES_PER_ISSUE = 5

#: 降级说明里最多列几条原因，其余指向引擎 meta。同理由。
MAX_REASONS_LISTED = 5

CATEGORY_FINDING = "现实合理性"
CATEGORY_DEGRADED = "引擎降级"

#: 按严重度给的处理口径。放常量表而不是逐处拼串，避免措辞漂移。
SUGGESTION_BY_SEVERITY = {
    "impossible": "该构件几何在数学上不成立，其工程量不可用：请核对图纸原图，"
                  "并复查识别/建模环节是否取错轮廓。",
    "implausible": "与规范下限或常见工程量级不符：请确认图纸标注，"
                   "若图纸确实如此则按设计变更处理。",
    "suspect": "仅为本模型内部的统计离群，未违反任何外部标准：请人工抽查确认。",
}


# ── 单张图 → 合理性模型 ──────────────────────────────────────────────────
def single_drawing_model(
    elements,
    *,
    drawing_id: str,
    label: str = "",
    discipline: str = "",
) -> PlausibilityModel:
    """把一张图的识别结果包成「一个单体 / 一层」的模型。

    `elements` 可以是 `FloorElements`，也可以是它的 `as_dict()`。
    刻意**复用 `from_scene`** 而不是另手搓一遍 Element：uid 形状、
    字段容错、层高推导只能有一处实现，否则单图与整楼两条路会慢慢走岔。

    产出的那一层没有标高、没有层高 —— 单张平面图本来就没有这些，
    需要它们的规则会据此报 `RuleNotApplicable`，这是预期内的降级。
    """
    raw = elements.as_dict() if hasattr(elements, "as_dict") else dict(elements or {})
    grouped = {kind: list(raw.get(kind) or []) for kind in ELEMENT_KINDS}
    scene = {
        "buildings": [{
            "key": drawing_id or "drawing",
            "label": label or drawing_id or "drawing",
            "floors": [{
                "key": SINGLE_FLOOR_KEY,
                "label": label or drawing_id or SINGLE_FLOOR_KEY,
                "order": 0,
                "elements": grouped,
            }],
        }],
    }
    model = from_scene(scene)
    return replace(model, meta={
        "source": "single_drawing",
        "drawing_id": drawing_id,
        "discipline": discipline,
        # 显式写死：这个模型没有标高，别让下游误以为是「标高恰好为 0」
        "has_elevation": False,
    })


def has_elements(elements) -> bool:
    """有没有可分析的构件。空结果与「没拿到结果」在调用处一起走降级。"""
    if elements is None:
        return False
    raw = elements.as_dict() if hasattr(elements, "as_dict") else elements
    if not isinstance(raw, dict):
        return False
    return any(raw.get(kind) for kind in ELEMENT_KINDS)


def elements_from_context(ctx: DrawingContext):
    """默认取数口径：视觉引擎写进 `ocr_metadata` 的识别结果。"""
    return (ctx.ocr_metadata or {}).get(ELEMENTS_METADATA_KEY)


# ── 聚合 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FindingGroup:
    """同一规则、同一严重度的一批结论，合成审图报告里的一条。"""
    rule: str
    severity: str
    basis: str
    kinds: tuple[str, ...]
    count: int
    samples: tuple[Finding, ...]


def aggregate(findings, *, samples: int = SAMPLES_PER_ISSUE) -> tuple[FindingGroup, ...]:
    """按 (规则, 严重度) 合并。

    **不只按规则合并**：同一条规则可能对不同构件给出不同档次的结论，
    混进一条就得在两个严重度里挑一个，挑哪个都是谎报。

    输入顺序即输出顺序（`Report.findings` 已由 `sort_findings` 定序），
    报告不随字典遍历漂移。
    """
    buckets: dict[tuple[str, str], list[Finding]] = {}
    for finding in findings:
        buckets.setdefault((finding.rule, finding.severity), []).append(finding)
    return tuple(
        FindingGroup(
            rule=rule, severity=severity, basis=items[0].basis,
            kinds=tuple(sorted({item.kind for item in items})),
            count=len(items), samples=tuple(items[:samples]),
        )
        for (rule, severity), items in buckets.items()
    )


def _fmt_number(value) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.4g}"
    text = str(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _fmt_evidence(evidence: dict) -> str:
    """证据串成 `k=v`。没有数字的审图意见没法复核，所以这段不能省。"""
    if not evidence:
        return "（该结论未附实测值）"
    return ", ".join(f"{key}={_fmt_number(val)}" for key, val in evidence.items())


def _describe(group: FindingGroup, denominator: int | None) -> str:
    head = (f"[{SEVERITY_LABELS[group.severity]}] {group.rule}"
            f"（{'/'.join(group.kinds)}）：命中 {group.count} 处")
    if denominator:
        head += f" / 检查 {denominator} 个（{group.count / denominator:.1%}）"
    lines = [head, f"依据：{group.basis}"]
    lines += [f"· {f.target}：{f.detail}｜{_fmt_evidence(f.evidence)}" for f in group.samples]
    remaining = group.count - len(group.samples)
    if remaining > 0:
        lines.append(f"（其余 {remaining} 处同类，未逐条列出）")
    return "\n".join(lines)


def _issue(severity: IssueSeverity, description: str, *, category: str,
           regulation_ref: str = "", suggestion: str = "") -> AIIssue:
    return AIIssue(engine=ENGINE_NAME, severity=severity, description=description,
                   category=category, regulation_ref=regulation_ref, suggestion=suggestion)


def _join_reasons(reasons: dict[str, str]) -> str:
    items = list(reasons.items())[:MAX_REASONS_LISTED]
    text = "；".join(f"{key}：{value}" for key, value in items)
    if len(reasons) > len(items):
        text += f"；（其余 {len(reasons) - len(items)} 条见引擎 meta）"
    return text


def degradation_issues(report: Report) -> list[AIIssue]:
    """把「没查」与「查崩了」变成看得见的条目。

    这不是审图问题而是**结果完整性声明**：少跑了规则却报「全部通过」，
    正是本仓库付过代价的那种静默失效。
    """
    issues: list[AIIssue] = []
    if report.skipped:
        issues.append(_issue(
            IssueSeverity.INFO,
            f"{len(report.skipped)} 条合理性规则未执行（缺数据，非通过）："
            f"{_join_reasons(report.skipped)}",
            category=CATEGORY_DEGRADED,
            suggestion="单张图纸没有标高与上下层关系，跨层规则需在整楼模型上复跑。"))
    if report.errored:
        # 比 INFO 高一档：这是代码缺陷导致的结果不完整，不该和大量 INFO 参考条目
        # 一起被过滤掉；但也不冒充图纸问题，所以不再往上抬。
        issues.append(_issue(
            IssueSeverity.MINOR,
            f"{len(report.errored)} 条合理性规则执行异常，结果不完整："
            f"{_join_reasons(report.errored)}",
            category=CATEGORY_DEGRADED,
            suggestion="属平台侧缺陷，请提交后台排查；本次合理性结论不可视为完整。"))
    return issues


def report_to_issues(report: Report, *, samples: int = SAMPLES_PER_ISSUE) -> list[AIIssue]:
    """`Report` → 审图问题列表（纯函数）。"""
    issues = [
        _issue(
            SEVERITY_MAP[group.severity],
            _describe(group, report.checked.get(group.rule)),
            category=CATEGORY_FINDING,
            # 依据出处复用「规范条文」列：它也可能是定理名或量纲式，
            # 但界面上只有这一列能显示依据，丢了就无法复核。
            regulation_ref=group.basis,
            suggestion=SUGGESTION_BY_SEVERITY.get(group.severity, ""),
        )
        for group in aggregate(report.findings, samples=samples)
    ]
    issues.extend(degradation_issues(report))
    return issues


# ── 引擎 ────────────────────────────────────────────────────────────────
class PlausibilityEngine(BaseEngine):
    """AI 审图的合理性引擎。契约同其它引擎：`analyze(ctx, db) -> list[AIIssue]`。

    `db` 不使用（本层无 DB 依赖），保留形参是为了让编排器一视同仁地调度。
    """
    engine_name = ENGINE_NAME

    def __init__(
        self,
        *,
        elements_provider: Callable[[DrawingContext], object] | None = None,
        rules: tuple | None = None,
        samples: int = SAMPLES_PER_ISSUE,
    ) -> None:
        self._provider = elements_provider or elements_from_context
        self._rules = rules            # None → 由 registry 现场加载规则族
        self._samples = samples
        #: 最近一次的原始报告与诊断信息。编排器可写进 `engine_results`，
        #: 让降级在库里也留痕，而不只是日志里闪一下。
        self.last_report: Report | None = None
        self.last_meta: dict = {}

    async def analyze(self, ctx: DrawingContext, db) -> list[AIIssue]:
        self.last_report = None
        self.last_meta = {"drawing_id": ctx.drawing_id}

        try:
            elements = self._provider(ctx)
        except Exception as exc:  # noqa: BLE001 - 取数失败要说出来，不能当成没问题
            return self._degraded(f"取构件识别结果失败：{type(exc).__name__}: {exc}")

        if not has_elements(elements):
            return self._degraded(
                "未拿到构件识别结果（视觉引擎未产出，或本图无可分析构件），"
                "合理性分析未执行")

        model = single_drawing_model(
            elements, drawing_id=ctx.drawing_id,
            label=ctx.drawing_no or ctx.title, discipline=ctx.discipline)

        try:
            report = registry.run(model, rules=self._rules)
        except Exception as exc:  # noqa: BLE001 - 规则表加载/执行崩了也必须可见
            logger.exception("[PlausibilityEngine] 图纸 %s 规则执行失败", ctx.drawing_no)
            return self._degraded(f"合理性规则执行失败：{type(exc).__name__}: {exc}")

        self.last_report = report
        self.last_meta.update({
            "elements": model.count(),
            "counts": report.counts,
            "checked": dict(report.checked),
            "skipped": dict(report.skipped),
            "errored": dict(report.errored),
        })
        if report.skipped or report.errored:
            logger.warning("[PlausibilityEngine] 图纸 %s 降级：未执行 %d 条、异常 %d 条",
                           ctx.drawing_no, len(report.skipped), len(report.errored))

        issues = report_to_issues(report, samples=self._samples)
        logger.info("[PlausibilityEngine] 图纸 %s 构件 %d 个，检出 %s，问题条目 %d",
                    ctx.drawing_no, model.count(), report.counts, len(issues))
        return issues

    def _degraded(self, reason: str) -> list[AIIssue]:
        """降级也要出一条 —— 返回空列表等于对外宣称「查过了，没问题」。"""
        self.last_meta["degraded"] = reason
        logger.warning("[PlausibilityEngine] %s", reason)
        return [_issue(IssueSeverity.INFO, reason, category=CATEGORY_DEGRADED,
                       suggestion="本次未产出合理性结论，不可据此认为模型合理。")]
