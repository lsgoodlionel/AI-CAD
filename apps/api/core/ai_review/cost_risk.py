"""图纸问题的**代价风险**分级 —— 从 4176 篇会议纪要 / 208 个工程实证得出。

数据来源:`docs/MEETING_MINUTES_MINING.md`(全语料 230,097 行统计,无采样、
无 LLM、可复现)。口径:LIFT = 该问题类型行中含**真·代价词**的比率
÷ 全语料基线率 0.47%。

**为什么需要它**:现有审图把问题按规则里写死的 `severity` 排序,
而 `severity` 表达的是「违规严重程度」,不是「会付出多少代价」。
实证显示两者并不一致 —— LIFT 最高的三项(施工顺序、设计变更、
图纸不一致)**都不是几何问题,而是「过程与版本」问题**,
恰是现有清单(纯几何/专业维度)的盲区。

**证据强度随数据一起走**:每个类型都带 `projects`(跨工程覆盖数)。
覆盖不足 `MIN_RELIABLE_PROJECTS` 的类型**不参与打分** ——
「碰撞」的 LIFT ×1.56 虽然反直觉地低,但它只来自 2/208 个工程,
证据本身就弱,不能拿它去压低碰撞在既有规则里的权重。
**反直觉的结论同样要受证据强度约束。**
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: 全语料含真·代价词的基线率(1073/230097),LIFT 的分母。
BASELINE_COST_RATE = 0.0047

#: 一个类型至少要在几个工程里出现过,其 LIFT 才可用作通用权重。
#: 取 5:208 个工程里只出现在 2~4 个的,与「工程特有词汇」难以区分。
MIN_RELIABLE_PROJECTS = 5


@dataclass(frozen=True)
class ProblemType:
    """一个问题类型的实证画像。`lift` 越高,该类问题越常伴随真实代价。"""

    name: str
    pattern: re.Pattern[str]
    lines: int             # 全语料出现行数
    lift: float            # 代价率 ÷ 基线率
    projects: int          # 跨工程覆盖数(208 个可识别工程中)

    @property
    def is_reliable(self) -> bool:
        """跨工程覆盖是否足以支撑通用权重。"""
        return self.projects >= MIN_RELIABLE_PROJECTS


def _t(name: str, pattern: str, lines: int, lift: float,
       projects: int) -> ProblemType:
    return ProblemType(name, re.compile(pattern), lines, lift, projects)


#: 实证问题类型表。数值全部来自 §3.2 的 LIFT 分析,不可凭直觉改动。
PROBLEM_TYPES: dict[str, ProblemType] = {
    t.name: t for t in (
        _t("施工顺序", r"施工顺序|工序穿插|穿插施工|先后顺序", 1478, 5.80, 15),
        _t("设计变更", r"设计变更|变更单|修改图|变更图|洽商单", 1204, 4.81, 11),
        _t("图纸不一致", r"图纸不[一致符]|图实不符|不一致|互相矛盾|前后矛盾",
           1341, 4.16, 13),
        _t("预留预埋", r"预留|预埋|留洞|留孔|套管", 3844, 3.18, 16),
        _t("深化滞后", r"深化设计滞后|深化图未|深化滞后|未及时深化", 1359, 3.00, 7),
        _t("选型未定", r"材料未定|型号未定|选型未|待选型|品牌未定", 5951, 2.52, 21),
        _t("规范不符", r"规范不符|不满足规范|验收不|不符合.{0,6}规范", 7242, 2.34, 19),
        _t("标高问题", r"标高", 2682, 2.32, 9),
        _t("尺寸偏差", r"尺寸|定位偏差|偏差", 2772, 2.01, 10),
        _t("做法不清", r"做法不[清明]|节点不[清明]|大样不[清明]", 6107, 1.72, 17),
        # ⚠ 仅 2/208 工程 —— 见 `is_reliable`,不参与打分
        _t("碰撞冲突", r"碰撞|管线冲突", 551, 1.56, 2),
    )
}

#: **真·代价词**。剔除计划性词后的集合 —— `拆除` 必须排除:
#: 它 28.9% 的高频几乎全是计划性拆除(「按图拆除原有隔墙」),
#: 第一版把它算进来,污染了整个 LIFT 排序。
_COST_WORDS_RE = re.compile(
    r"返工|返修|重做|二次(?:开洞|施工|搬运|处理)|凿除|剔凿|索赔|窝工"
    r"|停工待料|停工|影响工期|工期延误|报废|作废|增加费用|经济损失"
    r"|自行承担|承担费用|损失由|扣款|罚款")

#: 闭环信号句式(§3.4)。括号里是跨工程覆盖数,越高越通用。
_CLOSURE_PATTERNS: dict[str, re.Pattern[str]] = {
    # 责任单位指派(56/208,覆盖最广)
    "responsible_party": re.compile(
        r"(?:由|请)(?:总包|监理|业主|设计(?:院|单位)?|建设单位|施工单位|分包"
        r"|安装单位|土建单位|幕墙单位|钢构单位)[^\n，。]{0,10}"
        r"(?:负责|落实|完成|提供|明确|确认|出图|复核)"),
    # 时限条款(39/208)
    "deadline": re.compile(
        r"\d{1,2}月\d{1,2}日前|\d{1,2}日前(?:完成|提交|回复)"
        r"|\d{1,2}(?:个)?工作日内|一周内|本周内"),
    # **以 X 图为准(31/208)** —— 图纸版本冲突的裁决记录,
    # 等于人工标注的「多图冲突 → 执行图」金标签。现有清单提到
    # 「明确执行图」却没有任何抽取手段。
    "authoritative_drawing": re.compile(
        r"以[^\n，。]{2,25}图[^\n，。]{0,6}为准|按[^\n，。]{2,20}图施工"),
    # 决策悬置 = 未闭环(18/208)
    "pending_decision": re.compile(r"待(?:确认|定|明确|回复|设计确认|业主确认|复核)"),
    # 代价归属(13/208)
    "liability": re.compile(
        r"(?:由|归)[^\n，。]{2,12}(?:自行)?承担|损失由[^\n，。]{2,12}"
        r"|责任由[^\n，。]{2,12}"),
}


def classify_problem_types(text: str) -> list[str]:
    """文本命中的问题类型(**含不可靠类型**,供展示与统计)。"""
    body = str(text or "")
    if not body:
        return []
    return [t.name for t in PROBLEM_TYPES.values() if t.pattern.search(body)]


def cost_risk_score(text: str) -> float:
    """代价风险分 = 命中的**可靠**类型中的最高 LIFT;无命中 → 0。

    取最高而非累加:多个类型共现常常是同一问题的不同侧面
    (「预留预埋」与「图纸不一致」往往同句),累加会重复计价。
    """
    body = str(text or "")
    if not body:
        return 0.0
    hits = [t.lift for t in PROBLEM_TYPES.values()
            if t.is_reliable and t.pattern.search(body)]
    return max(hits) if hits else 0.0


def has_cost_consequence(text: str) -> bool:
    """是否出现**真·代价词**(已剔除计划性拆除)。"""
    return bool(_COST_WORDS_RE.search(str(text or "")))


def extract_closure_signals(text: str) -> dict[str, bool]:
    """闭环信号 → `{句式名: 是否命中}`。全部确定性正则,无 LLM。"""
    body = str(text or "")
    return {name: bool(pattern.search(body))
            for name, pattern in _CLOSURE_PATTERNS.items()}


def risk_profile(text: str) -> dict[str, Any]:
    """一次给出完整画像,供审图输出与排序使用。"""
    return {
        "problem_types": classify_problem_types(text),
        "cost_risk": round(cost_risk_score(text), 2),
        "has_cost_consequence": has_cost_consequence(text),
        "closure": extract_closure_signals(text),
    }


#: 与 `orchestrator._SEVERITY_ORDER` 同序（此处不 import，避免循环依赖）。
_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2, "suggestion": 3}


def issue_sort_key(severity: Any, text: str) -> tuple[int, float]:
    """审图问题的排序键：**先 severity，再代价风险**。

    `severity` 表达「违规严重程度」，代价风险表达「会付出多少代价」——
    实证显示两者不一致（LIFT 最高的三项都不是几何问题）。
    所以代价只在**同一 severity 之内**排序，不越权覆盖 severity：
    再高代价的 warning 也排在 critical 之后。

    代价取负值参与升序排序 —— 高代价在前。
    """
    key = getattr(severity, "value", severity)
    rank = _SEVERITY_RANK.get(str(key).lower(), 9)
    return rank, -cost_risk_score(text)
