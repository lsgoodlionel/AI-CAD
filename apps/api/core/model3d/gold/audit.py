"""金标准自审：真值本身也会错，能自动查的就别等人工发现。

**实测依据**：本阶段金标准出过三类错 ——
GPT 把有轴号的平面图判成非平面图（人工复核才推翻）、
分区序号错位致 F1 假跌 78%→68.6%、标签集合对上而位置贴错。
前两类靠人工发现，代价高。
"""
from __future__ import annotations

from .schema import MIN_CONFIDENCE, parse_unit


def _issue(code: str, unit: str, message: str, **extra) -> dict:
    return {"code": code, "unit": unit, "message": message, **extra}


def _audit_axes(unit_id: str, cls) -> list[dict]:
    """轴号要过 GB/T 50001 §8 校验，轴距链要自身闭合。"""
    from core.model3d.drawing_conventions import (
        parse_axis_label, validate_axis_labels)

    out: list[dict] = []
    # **按分区分开校验**：§8.0.5 要求的是分区**内**一致。第一版把跨分区的
    # 轴号当成一个序列，于是 `1-1` 与 `2-1` 被报成「轴号 1 重复」——
    # 31 条全是审计自身的假阳性。分区号可能写在 `zone` 字段里，
    # 也可能已含在轴号中（`1-A`），后者要靠解析取出。
    by_kind: dict[tuple, list[str]] = {}
    for inst in cls.instances:
        parsed = parse_axis_label(inst.id)
        zone = inst.zone if inst.zone is not None else (
            (parsed or {}).get("zone"))
        by_kind.setdefault((inst.kind or "unknown", zone), []).append(inst.id)
    for (kind, _zone), labels in by_kind.items():
        # 校验函数按「数字向/字母向」分，方向名先归一
        std_kind = ("numeric" if kind in ("vertical", "numeric")
                    else "alpha" if kind in ("horizontal", "alpha") else None)
        if std_kind is None:
            continue
        for violation in validate_axis_labels(labels, kind=std_kind):
            out.append(_issue("axis_label_violation", unit_id,
                              f"{kind}: {violation.get('message', violation)}"))

    # 轴距链：**末条不该有到下一条的间距**，否则档数与轴线数对不上
    with_gap = [i for i in cls.instances if i.to_next_mm]
    if with_gap:
        by_zone: dict = {}
        for inst in cls.instances:
            by_zone.setdefault((inst.zone, inst.kind), []).append(inst)
        for key, group in by_zone.items():
            gaps = sum(1 for i in group if i.to_next_mm)
            if gaps and gaps != len(group) - 1:
                out.append(_issue(
                    "chain_not_closed", unit_id,
                    f"{key}: 轴线 {len(group)} 条却有 {gaps} 档间距，"
                    f"应为 {len(group) - 1} 档"))
    return out


def audit_units(raw_units: list[dict] | None) -> list[dict]:
    """审一批金标准单元，返回问题清单（空表示无问题）。

    查四类：
      * **编号重复** —— 后一条会覆盖前一条，静默丢真值；
      * **轴号违反国标**（§8.0.3 类型/连续、§8.0.4 不用 I·O·Z、§8.0.5 分区一致）；
      * **轴距链不闭合** —— 档数必须等于轴线数减一；
      * **把握恰在阈值上又无人复核** —— 这类最容易被当成可信而其实没验过。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for raw in raw_units or []:
        try:
            unit = parse_unit(raw)
        except ValueError as exc:
            out.append(_issue("unparsable", str(raw.get("unit", "?")), str(exc)))
            continue
        if unit.unit in seen:
            out.append(_issue("duplicate_unit", unit.unit,
                              "编号重复——后一条会覆盖前一条"))
        seen.add(unit.unit)

        for name, cls in unit.classes.items():
            if (cls.excluded or "").strip():
                continue
            if (not cls.verified_by
                    and abs(cls.confidence - MIN_CONFIDENCE) < 1e-9):
                out.append(_issue(
                    "unverified_at_threshold", unit.unit,
                    f"{name}: 把握恰为阈值 {MIN_CONFIDENCE} 且无人复核，建议复查"))
            if name == "axes" and cls.method == "instances":
                out.extend(_audit_axes(unit.unit, cls))
    return out
