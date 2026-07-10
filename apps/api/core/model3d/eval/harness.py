"""三方法评测编排：纯规则 vs 学习模型 vs 融合 → 可复现对比报告。

- **rule**：确定性弱标签（auto_label / element_recognizer 体系）→ SymbolCandidate(source=rule)。
- **model**：spotting 服务（C-12；无 GPU/权重时 mock 兜底）→ SymbolCandidate(source=model)。
- **fusion**：C-13 融合（规则强命中不被覆盖 + 模型补召回）。

诚实边界：C-09 真实权重就绪前 model 端由 mock 代入（规则派生），与 rule 近同，对比暂
为占位；harness 与指标已就绪，权重到位后同一基座直接评真实模型（报告显式标注该状态）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.model3d.dataset.auto_label import auto_label
from core.model3d.fusion import fuse
from core.model3d.preprocess.schema import PrimitiveDoc
from core.model3d.spotting.types import SymbolCandidate

from .metrics import GtBox, MethodMetrics, evaluate

# 类别 → 专业（对齐 C-05 DATASET_SPEC：结构=柱梁板墙，建筑=门窗，机电=管/设备）
_CATEGORY_DISCIPLINE = {
    "column": "结构", "beam": "结构", "slab": "结构", "wall": "结构",
    "door": "建筑", "window": "建筑",
    "pipe": "机电", "equipment": "机电",
    "axis": "通用",
}

METHODS = ("rule", "model", "fusion")


def discipline_of(category: str) -> str:
    return _CATEGORY_DISCIPLINE.get(category, "未标注")


@dataclass(frozen=True)
class EvalSample:
    """单张图纸的评测样本：图元文档 + 金标签真值。"""
    doc: PrimitiveDoc
    gt: tuple[GtBox, ...] = ()
    sample_id: str = ""


@dataclass(frozen=True)
class ComparisonReport:
    """三方法对比报告。"""
    methods: dict[str, MethodMetrics] = field(default_factory=dict)
    sample_count: int = 0
    iou_thr: float = 0.5
    ceiling: dict | None = None  # SymPoint 天花板参照（C-11，隔离环境回流数字）
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "sample_count": self.sample_count,
            "iou_thr": self.iou_thr,
            "methods": {k: v.to_dict() for k, v in self.methods.items()},
            "ceiling": self.ceiling,
            "notes": list(self.notes),
        }


def _bbox_of(points: tuple[tuple[float, float], ...]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def rule_candidates(doc: PrimitiveDoc) -> list[SymbolCandidate]:
    """确定性规则预测（auto_label 弱标签 → 候选，source=rule）。"""
    prim_by_id = {p.id: p for p in doc.primitives}
    out: list[SymbolCandidate] = []
    for lp in auto_label(doc).labeled:
        if lp.category is None:
            continue
        prim = prim_by_id.get(lp.primitive_id)
        if prim is None:
            continue
        bbox = _bbox_of(prim.points)
        if bbox is None:
            continue
        out.append(
            SymbolCandidate(
                category=lp.category, confidence=float(lp.confidence or 0.5),
                bbox=bbox, source="rule", mep_system=lp.mep_system,
                primitive_ids=(lp.primitive_id,),
            )
        )
    return out


def model_candidates(doc: PrimitiveDoc, service=None) -> list[SymbolCandidate]:
    """学习模型预测（spotting 服务，无权重时 mock 兜底，source=model）。"""
    if service is None:
        from core.model3d.spotting.service import SpottingService

        service = SpottingService(db=None)
    return list(service.spot_doc(doc).candidates)


def fusion_candidates(
    rule: list[SymbolCandidate], model: list[SymbolCandidate]
) -> list[SymbolCandidate]:
    return list(fuse(rule, model).candidates)


def run_comparison(
    samples: list[EvalSample],
    *,
    service=None,
    iou_thr: float = 0.5,
    ceiling: dict | None = None,
) -> ComparisonReport:
    """在样本集上跑三方法对比（预测跨样本展平后统一评测）。"""
    preds: dict[str, list] = {m: [] for m in METHODS}
    all_gt: list[GtBox] = []
    for sample in samples:
        all_gt.extend(sample.gt)
        rule = rule_candidates(sample.doc)
        model = model_candidates(sample.doc, service=service)
        preds["rule"].extend(rule)
        preds["model"].extend(model)
        preds["fusion"].extend(fusion_candidates(rule, model))

    methods = {
        m: evaluate(all_gt, preds[m], iou_thr=iou_thr, discipline_of=discipline_of)
        for m in METHODS
    }
    notes = (
        "model 端当前由 spotting mock 兜底（规则派生），C-09 真实权重就绪后同一基座复评；"
        "test 集须按 C-07 项目切分冻结，仅 C-18 终评解冻一次。",
    )
    return ComparisonReport(
        methods=methods, sample_count=len(samples), iou_thr=iou_thr,
        ceiling=ceiling, notes=notes,
    )
