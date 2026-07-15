"""D-18 GraphRAG 合规审查评测度量（对齐 docs/PHASE_D_GRAPHRAG.md §3）。

匹配单元：合规审查的输出不是 bbox，不能直接照搬 `core/model3d/eval/metrics.py`
的 IoU 匹配。本模块把「判定单元」定义为 `(drawing_id, regulation_ref_normalized,
discipline)`（doc §3.1），但工程实现上做了一处刻意简化并显式记录取舍：

    **按样本（单张图纸）分别评测，再用 `aggregate_compliance_metrics` 汇总**，
    而不是把多张图纸的真值/预测一次性展平后统一匹配（`core/model3d/eval` 对 bbox
    是展平统一匹配的，因为 bbox 位置天然把跨图片的框分开了）。原因：
    `core.ai_review.base.AIIssue`（预测的载体）**没有 `drawing_id` 字段**——
    审图问题按 `report_id` 归属图纸，不是每条 issue 自带。要支持展平匹配就必须
    给 `AIIssue` 加字段，但 `core/ai_review/base.py` 不在本轮任务的文件边界内
    （见任务描述“⛔ 不碰 …”）。按样本评测规避了这个问题，且语义上更正确：
    永远不会把 A 图纸的真值错配到 B 图纸的预测。调用方（`harness.py`）对每个
    `ComplianceEvalSample`（天然=一张图纸）单独调用 `evaluate_compliance`，再
    `aggregate_compliance_metrics` 求和推导整体指标。

度量口径（doc §3.2/§3.3/§3.4）：
    - TP：判定单元匹配 + severity 差 ≤1 级。
    - 判定单元匹配：`regulation_ref` 归一化后精确相等；任一方缺失引用时退化为
      `ComplianceGt.snippet` 与 `AIIssue.description` 的文本相似度
      （`difflib.SequenceMatcher`，与 `fusion.py::_merge_and_dedup` 同一套阈值
      语义，默认阈值也保持一致 0.72，避免评测口径和融合口径互相打架）。
    - severity 差 ≥2 级：**本实现选择计入 FP（tag=wrong_severity），不计入 FN**
      ——因为「问题定位对了」不该被当成漏报，但「严重度判断离谱」也不该被当成
      合格 TP。doc §3.2 原文说这类候选「不计入主 Precision/Recall」，但 §3.4 又把
      它列进 FP 细分表，两处表述在文档里本身不完全自洽（该文档是评价标准的
      *建议*，非既成事实）；本实现选择「计入 FP 但不计入 FN」这一单一、无歧义
      的核算方式，避免同一候选被两处双重处罚，并在此显式记录该取舍。
    - 条文引用命中率：仅统计 TP 子集中 `regulation_ref` 精确命中的占比——衡量
      「问题方向对了但引错条文」的比例（该子情形本身计入 wrong_ref FP，不计入
      TP，因此这里统计的是「真正答对」的 TP 里有多少精确到条文）。
    - MUST/SHOULD/MAY/MUST_NOT 混淆矩阵：仅统计 TP 子集（doc §3.3）。
    - 义务降级率：`(MUST 行内非 MUST 列之和) / MUST 行合计`（红线指标，doc §3.3）。
    - FN 细分（`kg_missed_rag_missed` / `retrieved_but_llm_dropped`）：需要
      「LLM 核查前的合并候选池」才能精确区分，这是可选的 `retrieval_universe`
      参数；不传时保守全部归为 `kg_missed_rag_missed`（诚实边界：无法比确定性
      更强的结论）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from core.ai_review.base import AIIssue

# 与 core/ai_review/graphrag/types.py::FusionConfig.dedup_similarity_threshold
# 保持同一默认值，避免评测口径与融合口径各说各话。
DEFAULT_SIMILARITY_THRESHOLD = 0.72

# severity 差 <= 此值仍算 TP（doc §3.2：「不要求 severity 完全相等，但不能一个
# 说 critical 一个说 info」）。
_SEVERITY_TP_TOLERANCE = 1

_SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "critical": 3}
_VALID_OBLIGATIONS = {"MUST", "SHOULD", "MAY", "MUST_NOT"}
_OBLIGATION_PREFIX_RE = re.compile(r"^\[(MUST_NOT|MUST|SHOULD|MAY)\]")

_EMPTY_FP_BREAKDOWN = {"spurious": 0, "wrong_ref": 0, "wrong_severity": 0}
_EMPTY_FN_BREAKDOWN = {"kg_missed_rag_missed": 0, "retrieved_but_llm_dropped": 0}


@dataclass(frozen=True)
class ComplianceGt:
    """金标准合规问题（人工标注 或 `bootstrap_gold` 从人审动作埋点回流）。"""

    drawing_id: str
    regulation_ref: str
    discipline: str = ""
    obligation_level: str = "SHOULD"  # MUST/SHOULD/MAY/MUST_NOT
    severity: str = "major"           # critical/major/minor/info
    # 未标注条文时的文本相似度退化匹配来源（对齐 fusion.py 同款兜底策略）。
    snippet: str = ""

    def to_dict(self) -> dict:
        return {
            "drawing_id": self.drawing_id,
            "regulation_ref": self.regulation_ref,
            "discipline": self.discipline,
            "obligation_level": self.obligation_level,
            "severity": self.severity,
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class ComplianceMetrics:
    """单批（单样本或已聚合的多样本）合规评测指标。"""

    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    ref_hit_count: int                       # TP 中条文引用精确命中的原始计数
    regulation_hit_rate: float                # = ref_hit_count / tp
    obligation_confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    obligation_downgrade_rate: float = 0.0
    fp_breakdown: dict[str, int] = field(default_factory=lambda: dict(_EMPTY_FP_BREAKDOWN))
    fn_breakdown: dict[str, int] = field(default_factory=lambda: dict(_EMPTY_FN_BREAKDOWN))
    sample_count: int = 1

    def to_dict(self) -> dict:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "ref_hit_count": self.ref_hit_count,
            "regulation_hit_rate": round(self.regulation_hit_rate, 4),
            "obligation_confusion": self.obligation_confusion,
            "obligation_downgrade_rate": round(self.obligation_downgrade_rate, 4),
            "fp_breakdown": dict(self.fp_breakdown),
            "fn_breakdown": dict(self.fn_breakdown),
            "sample_count": self.sample_count,
        }


# ──────────────────────── 归一化 / 提取（纯函数） ────────────────────────

def normalize_regulation_ref(ref: str, *, strip_version_year: bool = True) -> str:
    """条文引用归一化：去空白、转大写；可选剥离规范号年份后缀
    （如 ``GB50010-2010`` → ``GB50010``），使版本差异不阻断判定单元匹配。
    """
    if not ref:
        return ""
    s = re.sub(r"\s+", "", ref.strip()).upper()
    if strip_version_year:
        s = re.sub(r"^([A-Z]+[0-9.]+)-\d{2,4}", r"\1", s)
    return s


def extract_obligation_level(description: str) -> str:
    """从 `description` 的 `[MUST]`/`[SHOULD]`/... 前缀提取义务等级
    （与 `fusion.py::_parse_verify_response` 的编码约定一致）；无前缀时按该模块
    「无法判断填 SHOULD」的同一约定回退。
    """
    match = _OBLIGATION_PREFIX_RE.match(description or "")
    level = match.group(1) if match else "SHOULD"
    return level if level in _VALID_OBLIGATIONS else "SHOULD"


def _text_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _severity_diff(gt_severity: str, pred_severity) -> int:
    pred_str = pred_severity.value if hasattr(pred_severity, "value") else str(pred_severity)
    gt_rank = _SEVERITY_RANK.get(str(gt_severity or "major").lower(), 2)
    pred_rank = _SEVERITY_RANK.get(pred_str.lower(), 2)
    return abs(gt_rank - pred_rank)


# ──────────────────────── 判定单元匹配（纯函数） ────────────────────────

def _match(
    gt: list[ComplianceGt],
    pred: list[AIIssue],
    *,
    similarity_threshold: float,
) -> tuple[list[tuple[int, int, bool]], set[int], set[int]]:
    """贪心匹配：条文归一化精确相等（score=1.0，`ref_exact=True`）优先；
    双方均缺失/不相等条文引用时退化为文本相似度（`snippet` vs `description`）。
    与 `core/model3d/eval/metrics.py::_match` 同一种「打分排序 + 贪心占用」结构，
    只是把 IoU 换成「判定单元」得分。
    """
    pairs: list[tuple[float, int, int, bool]] = []
    for gi, g in enumerate(gt):
        g_ref = normalize_regulation_ref(g.regulation_ref)
        for pi, p in enumerate(pred):
            p_ref = normalize_regulation_ref(p.regulation_ref)
            if g_ref and p_ref:
                if g_ref == p_ref:
                    pairs.append((1.0, gi, pi, True))
                continue  # 双方都标了条文但不相等——不构成候选匹配，交给 FP/FN 侧处理
            score = _text_similarity(g.snippet, p.description)
            if score >= similarity_threshold:
                pairs.append((score, gi, pi, False))

    pairs.sort(key=lambda t: t[0], reverse=True)
    used_gt: set[int] = set()
    used_pred: set[int] = set()
    matched: list[tuple[int, int, bool]] = []
    for score, gi, pi, ref_exact in pairs:
        if gi in used_gt or pi in used_pred:
            continue
        used_gt.add(gi)
        used_pred.add(pi)
        matched.append((gi, pi, ref_exact))

    unmatched_gt = set(range(len(gt))) - used_gt
    unmatched_pred = set(range(len(pred))) - used_pred
    return matched, unmatched_gt, unmatched_pred


def _best_text_similarity(description: str, gt: list[ComplianceGt]) -> float:
    if not gt:
        return 0.0
    return max((_text_similarity(description, g.snippet) for g in gt), default=0.0)


def _gt_matches_universe(
    g: ComplianceGt, universe: list[AIIssue], similarity_threshold: float,
) -> bool:
    g_ref = normalize_regulation_ref(g.regulation_ref)
    for u in universe:
        u_ref = normalize_regulation_ref(u.regulation_ref)
        if g_ref and u_ref and g_ref == u_ref:
            return True
        if _text_similarity(g.snippet, u.description) >= similarity_threshold:
            return True
    return False


def _build_obligation_confusion(
    gt: list[ComplianceGt], pred: list[AIIssue], matched_ok: list[tuple[int, int, bool]],
) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {}
    for gi, pi, _ in matched_ok:
        row = (gt[gi].obligation_level or "SHOULD").upper()
        row = row if row in _VALID_OBLIGATIONS else "SHOULD"
        col = extract_obligation_level(pred[pi].description)
        matrix.setdefault(row, {})
        matrix[row][col] = matrix[row].get(col, 0) + 1
    return matrix


def _confusion_downgrade_rate(confusion: dict[str, dict[str, int]]) -> float:
    must_row = confusion.get("MUST", {})
    total = sum(must_row.values())
    if total == 0:
        return 0.0
    non_must = sum(v for k, v in must_row.items() if k != "MUST")
    return non_must / total


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


# ──────────────────────── 主评测函数 ────────────────────────

def evaluate_compliance(
    gt: list[ComplianceGt],
    pred: list[AIIssue],
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    retrieval_universe: list[AIIssue] | None = None,
) -> ComplianceMetrics:
    """在单样本（同一张图纸）的真值/预测上计算全指标（doc §3.2/§3.3/§3.4）。

    ``retrieval_universe``：可选，LLM 核查前的合并候选池（如
    `GraphRAGFusionResult` 降级路径产出的候选直出 issues）。提供时才能把 FN
    细分为 `kg_missed_rag_missed`（双路都没召回）vs `retrieved_but_llm_dropped`
    （召回到了但 LLM 核查环节剔除）；不提供时保守全部计入
    `kg_missed_rag_missed`。
    """
    matched, unmatched_gt, unmatched_pred = _match(
        gt, pred, similarity_threshold=similarity_threshold,
    )

    matched_ok: list[tuple[int, int, bool]] = []
    matched_severity_off: list[tuple[int, int]] = []
    for gi, pi, ref_exact in matched:
        if _severity_diff(gt[gi].severity, pred[pi].severity) <= _SEVERITY_TP_TOLERANCE:
            matched_ok.append((gi, pi, ref_exact))
        else:
            matched_severity_off.append((gi, pi))

    tp = len(matched_ok)
    ref_hit_count = sum(1 for _, _, ref_exact in matched_ok if ref_exact)

    wrong_ref_count = 0
    spurious_count = 0
    for pi in unmatched_pred:
        if _best_text_similarity(pred[pi].description, gt) >= similarity_threshold:
            wrong_ref_count += 1
        else:
            spurious_count += 1
    wrong_severity_count = len(matched_severity_off)
    fp = len(unmatched_pred) + wrong_severity_count

    fn = len(unmatched_gt)
    kg_missed = 0
    llm_dropped = 0
    for gi in unmatched_gt:
        if retrieval_universe is not None and _gt_matches_universe(
            gt[gi], retrieval_universe, similarity_threshold,
        ):
            llm_dropped += 1
        else:
            kg_missed += 1

    obligation_confusion = _build_obligation_confusion(gt, pred, matched_ok)
    precision, recall, f1 = _prf(tp, fp, fn)

    return ComplianceMetrics(
        tp=tp, fp=fp, fn=fn,
        precision=precision, recall=recall, f1=f1,
        ref_hit_count=ref_hit_count,
        regulation_hit_rate=(ref_hit_count / tp) if tp else 0.0,
        obligation_confusion=obligation_confusion,
        obligation_downgrade_rate=_confusion_downgrade_rate(obligation_confusion),
        fp_breakdown={
            "spurious": spurious_count,
            "wrong_ref": wrong_ref_count,
            "wrong_severity": wrong_severity_count,
        },
        fn_breakdown={"kg_missed_rag_missed": kg_missed, "retrieved_but_llm_dropped": llm_dropped},
        sample_count=1,
    )


# ──────────────────────── 跨样本聚合（纯函数） ────────────────────────

def _sum_dicts(dicts: list[dict[str, int]], keys: set[str]) -> dict[str, int]:
    return {k: sum(d.get(k, 0) for d in dicts) for k in keys}


def _sum_confusion(confusions: list[dict[str, dict[str, int]]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for c in confusions:
        for row, cols in c.items():
            out.setdefault(row, {})
            for col, count in cols.items():
                out[row][col] = out[row].get(col, 0) + count
    return out


def aggregate_compliance_metrics(metrics_list: list[ComplianceMetrics]) -> ComplianceMetrics:
    """把按样本评测出的 `ComplianceMetrics` 求和聚合为整体报告（纯函数、确定性）。

    tp/fp/fn/ref_hit_count/breakdown/confusion 直接求和，precision/recall/f1/
    regulation_hit_rate/obligation_downgrade_rate 用求和后的原始计数重新推导
    （而非对各样本比率取平均——比率不可加，必须回到分子分母求和后再算）。
    """
    if not metrics_list:
        return ComplianceMetrics(
            tp=0, fp=0, fn=0, precision=0.0, recall=0.0, f1=0.0,
            ref_hit_count=0, regulation_hit_rate=0.0,
            obligation_confusion={}, obligation_downgrade_rate=0.0,
            fp_breakdown=dict(_EMPTY_FP_BREAKDOWN), fn_breakdown=dict(_EMPTY_FN_BREAKDOWN),
            sample_count=0,
        )

    tp = sum(m.tp for m in metrics_list)
    fp = sum(m.fp for m in metrics_list)
    fn = sum(m.fn for m in metrics_list)
    ref_hit_count = sum(m.ref_hit_count for m in metrics_list)
    fp_breakdown = _sum_dicts([m.fp_breakdown for m in metrics_list], set(_EMPTY_FP_BREAKDOWN))
    fn_breakdown = _sum_dicts([m.fn_breakdown for m in metrics_list], set(_EMPTY_FN_BREAKDOWN))
    confusion = _sum_confusion([m.obligation_confusion for m in metrics_list])
    precision, recall, f1 = _prf(tp, fp, fn)

    return ComplianceMetrics(
        tp=tp, fp=fp, fn=fn,
        precision=precision, recall=recall, f1=f1,
        ref_hit_count=ref_hit_count,
        regulation_hit_rate=(ref_hit_count / tp) if tp else 0.0,
        obligation_confusion=confusion,
        obligation_downgrade_rate=_confusion_downgrade_rate(confusion),
        fp_breakdown=fp_breakdown, fn_breakdown=fn_breakdown,
        sample_count=sum(m.sample_count for m in metrics_list),
    )
