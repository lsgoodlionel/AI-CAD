"""OCR 后端对比报告 → Markdown 渲染（有金标签 / 无金标签两套报告）。"""
from __future__ import annotations

from .harness import OcrComparisonReport
from .unlabeled import AgreementMetrics, UnlabeledComparisonReport

_KIND_LABEL = {"elevation": "标高", "axis": "轴号", "title": "图名·房间名"}


def _fmt(v: float) -> str:
    return f"{v:.3f}"


def _fmt_calibration(v: float | None) -> str:
    return _fmt(v) if v is not None else "N/A（样本不足/退化）"


def _fmt_agreement(a: AgreementMetrics) -> str:
    return f"{a.matched}/{a.only_a}/{a.only_b}/{_fmt_calibration(a.jaccard)}"


def render_markdown(
    report: OcrComparisonReport, *, title: str = "D-16 OCR 后端评测对比报告"
) -> str:
    lines: list[str] = [f"# {title}", ""]
    lines.append(
        f"> 样本数：{report.sample_count} | 标高匹配容差：±{report.elevation_tolerance_m}m"
    )
    lines.append("")

    if not report.backends:
        lines.append("（无后端参与评测）")
        lines.append("")
        return "\n".join(lines)

    # 总体：每类 token 的 Precision/Recall/F1，按后端分表
    for kind in ("elevation", "axis", "title"):
        lines.append(f"## {_KIND_LABEL[kind]}（{kind}）")
        lines.append("")
        lines.append("| 后端 | Precision | Recall | F1 | TP | FP | FN | 置信标定 r_pb |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for name, bm in report.backends.items():
            metrics = getattr(bm, kind)
            calibration = getattr(bm, f"{kind}_calibration")
            lines.append(
                f"| {name} | {_fmt(metrics.precision)} | {_fmt(metrics.recall)} | "
                f"{_fmt(metrics.f1)} | {metrics.tp} | {metrics.fp} | {metrics.fn} | "
                f"{_fmt_calibration(calibration)} |"
            )
        lines.append("")

    # 可用性 + 整体置信标定
    lines.append("## 后端可用性与整体置信标定")
    lines.append("")
    lines.append("| 后端 | 样本数 | 不可用样本数 | 整体 r_pb | 告警 |")
    lines.append("|---|---|---|---|---|")
    for name, bm in report.backends.items():
        warn_text = "; ".join(bm.warnings) if bm.warnings else "-"
        lines.append(
            f"| {name} | {bm.sample_count} | {bm.unavailable_samples} | "
            f"{_fmt_calibration(bm.overall_calibration)} | {warn_text} |"
        )
    lines.append("")

    # 结论摘要：按 F1 挑每类 token 的最优可用后端
    lines.append("## 结论摘要")
    lines.append("")
    for kind in ("elevation", "axis", "title"):
        usable = {
            name: getattr(bm, kind)
            for name, bm in report.backends.items()
            if bm.sample_count > bm.unavailable_samples  # 至少跑起来过一次
        }
        if not usable:
            lines.append(f"- {_KIND_LABEL[kind]}：本轮无可用后端产出预测。")
            continue
        best_name = max(usable, key=lambda n: usable[n].f1)
        lines.append(
            f"- {_KIND_LABEL[kind]}：F1 最优后端为 **{best_name}** "
            f"({_fmt(usable[best_name].f1)})。"
        )
    lines.append("")

    for note in report.notes:
        lines.append(f"> 注：{note}")
    lines.append("")
    return "\n".join(lines)


def render_unlabeled_markdown(
    report: UnlabeledComparisonReport,
    *,
    title: str = "D-16 OCR 后端无金标签评测报告（一致性 / 识别量 / 三馈线产出量）",
) -> str:
    """无金标签模式报告：不产出 Precision/Recall（无真值），只产出可横向比较
    后端强弱的三张表——识别量与置信分布、三馈线产出量、后端间一致性。
    """
    lines: list[str] = [f"# {title}", ""]
    lines.append(
        f"> 样本数：{report.sample_count} | 标高一致性容差：±{report.elevation_tolerance_m}m | "
        "**无金标签：以下指标为后端间横向比较，不是准确率**"
    )
    lines.append("")

    if not report.backends:
        lines.append("（无后端参与评测）")
        lines.append("")
        return "\n".join(lines)

    # 识别量与置信分布
    lines.append("## 识别量与置信分布（按 kind）")
    lines.append("")
    lines.append("| 后端 | kind | 数量 | 置信均值 | 置信中位数 | 置信最小 | 置信最大 |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, bm in report.backends.items():
        for kind, stats in bm.confidence_by_kind.items():
            if stats.count == 0:
                continue
            lines.append(
                f"| {name} | {kind} | {stats.count} | {_fmt(stats.mean)} | "
                f"{_fmt(stats.median)} | {_fmt(stats.min)} | {_fmt(stats.max)} |"
            )
    lines.append("")

    # 三馈线产出量
    lines.append("## 三馈线产出量（consume.py，默认置信门槛过滤后）")
    lines.append("")
    lines.append("| 后端 | elevation_candidates | axis_anchors | space_labels |")
    lines.append("|---|---|---|---|")
    for name, bm in report.backends.items():
        lines.append(
            f"| {name} | {bm.consume_elevation_count} | {bm.consume_axis_count} | "
            f"{bm.consume_title_count} |"
        )
    lines.append("")

    # 可用性
    lines.append("## 后端可用性")
    lines.append("")
    lines.append("| 后端 | 样本数 | 不可用样本数 | 告警 |")
    lines.append("|---|---|---|---|")
    for name, bm in report.backends.items():
        warn_text = "; ".join(bm.warnings) if bm.warnings else "-"
        lines.append(f"| {name} | {bm.sample_count} | {bm.unavailable_samples} | {warn_text} |")
    lines.append("")

    # 后端间一致性
    lines.append("## 后端间一致性（无真值，matched/only_A/only_B/Jaccard）")
    lines.append("")
    if not report.pairwise:
        lines.append("（不足两个后端参与评测，无法比较）")
        lines.append("")
    else:
        lines.append("| A | B | 可比样本数 | 标高 | 轴号 | 图名·房间名 |")
        lines.append("|---|---|---|---|---|---|")
        for pa in report.pairwise:
            lines.append(
                f"| {pa.backend_a} | {pa.backend_b} | {pa.comparable_samples} | "
                f"{_fmt_agreement(pa.elevation)} | {_fmt_agreement(pa.axis)} | "
                f"{_fmt_agreement(pa.title)} |"
            )
        lines.append("")

    for note in report.notes:
        lines.append(f"> 注：{note}")
    lines.append("")
    return "\n".join(lines)
