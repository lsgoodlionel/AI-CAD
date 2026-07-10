"""评测对比报告 → Markdown 渲染（可回归、滚动更新 docs/PHASE_C_EVAL_REPORT.md）。"""
from __future__ import annotations

from .harness import METHODS, ComparisonReport

_METHOD_LABEL = {"rule": "纯规则", "model": "学习模型", "fusion": "融合"}


def _fmt(v: float) -> str:
    return f"{v:.3f}"


def render_markdown(report: ComparisonReport, *, title: str = "Phase C 评测对比报告") -> str:
    lines: list[str] = [f"# {title}", ""]
    lines.append(f"> 样本数：{report.sample_count} | IoU 阈值：{report.iou_thr}")
    lines.append("")

    # 总体三方法对比
    lines.append("## 1. 总体指标（纯规则 vs 学习模型 vs 融合）")
    lines.append("")
    lines.append("| 方法 | PQ | 精度 | 召回 | F1 | TP | FP | FN |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for m in METHODS:
        mm = report.methods.get(m)
        if mm is None:
            continue
        lines.append(
            f"| {_METHOD_LABEL[m]} | {_fmt(mm.pq)} | {_fmt(mm.precision)} | "
            f"{_fmt(mm.recall)} | {_fmt(mm.f1)} | {mm.tp} | {mm.fp} | {mm.fn} |"
        )
    lines.append("")

    # 天花板参照
    if report.ceiling:
        lines.append("## 2. 天花板参照（SymPoint，C-11 隔离环境回流数字）")
        lines.append("")
        lines.append(f"> {report.ceiling}")
        lines.append("")

    # 分专业（以融合为例）
    fusion = report.methods.get("fusion")
    if fusion and fusion.per_discipline:
        lines.append("## 3. 分专业（融合方法）")
        lines.append("")
        lines.append("| 专业 | PQ | 精度 | 召回 | TP | FP | FN |")
        lines.append("|---|---|---|---|---|---|---|")
        for disc, d in fusion.per_discipline.items():
            lines.append(
                f"| {disc} | {d['pq']} | {d['precision']} | {d['recall']} | "
                f"{d['tp']} | {d['fp']} | {d['fn']} |"
            )
        lines.append("")

    # 分类别对比（三方法 PQ）
    lines.append("## 4. 分类别 PQ（三方法对比）")
    lines.append("")
    cats = sorted(
        {c for m in METHODS if (mm := report.methods.get(m)) for c in mm.per_category}
    )
    lines.append("| 类别 | 纯规则 PQ | 学习模型 PQ | 融合 PQ |")
    lines.append("|---|---|---|---|")
    for cat in cats:
        row = [cat]
        for m in METHODS:
            mm = report.methods.get(m)
            cell = mm.per_category.get(cat, {}).get("pq", "-") if mm else "-"
            row.append(str(cell))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # 结论摘要（学习/融合在哪些类别超过纯规则）
    lines.append("## 5. 结论摘要")
    lines.append("")
    rule = report.methods.get("rule")
    if rule and fusion:
        wins = [
            cat for cat in cats
            if fusion.per_category.get(cat, {}).get("pq", 0)
            > rule.per_category.get(cat, {}).get("pq", 0)
        ]
        lines.append(
            f"- 融合 PQ 超过纯规则的类别：{wins or '（当前无——model 端为 mock 占位，待 C-09）'}"
        )
        lines.append(
            f"- 融合总体 PQ {_fmt(fusion.pq)} vs 纯规则 {_fmt(rule.pq)}"
            f"（融合召回≥纯规则为结构性保证）"
        )
    lines.append("")

    for note in report.notes:
        lines.append(f"> 注：{note}")
    lines.append("")
    return "\n".join(lines)
