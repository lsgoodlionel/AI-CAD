"""设计说明参数提取（GB 50010 / GB 50011 / GB 1499 等）。

**方向调整的产物**：本轮试过三条「复原条文」的路径（x 聚类分栏 →
XY-cut 分块 → 按块重组），条文数始终停在 8 条、平均 4000+ 字的拼接 ——
OCR 输出乱序加上说明版面不规则（分块套多栏），需要专门的版面模型。

**但读设计说明的目的是拿参数，不是复原条文全文。**
参数有固定的书写规范，可以直接从行里提，**完全不需要版面恢复**。

这些参数正是建模与审图真正需要的：混凝土强度决定构件材料属性、
抗震等级决定构造要求、保护层厚度进算量、人防等级决定专项审查口径。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: GB 50010 混凝土强度等级：C15~C80，**5 的倍数**。
#: 后面不能跟 `@`（`C12@200` 是钢筋规格）或 `-`/字母（`C65H` 是断路器型号）。
_CONCRETE_RE = re.compile(r"C(15|[2-7][05]|80)(?![\d@\-A-Za-z])")

#: GB 1499 钢筋牌号。
_REBAR_RE = re.compile(r"(HRB\s?(?:335|400|500)E?|HPB\s?300|RRB\s?400)")

#: GB 50011 抗震等级（含特一级）。
_SEISMIC_RE = re.compile(r"抗震等级[^一二三四特]{0,6}(特一级|[一二三四]级)")

#: 保护层厚度 —— 取数值与单位。
_COVER_RE = re.compile(r"保护层厚度[^0-9]{0,10}(\d{1,3})\s*mm")

#: 人防抗力级别。
_DEFENSE_RE = re.compile(r"((?:核|常)\s?[1-6][a-z]?级)")

#: 保护层的合理区间（mm）。GB 50010 表 8.2.1 最小 15、最大约 70；
#: 放宽到 10~100 容差，超出即不是保护层（实测 `500mm` 是别的数）。
MIN_COVER_MM = 10.0
MAX_COVER_MM = 100.0


@dataclass(frozen=True)
class SpecParameter:
    """一条从设计说明提取的标准参数。"""
    kind: str            # concrete_grade / rebar_grade / seismic_level /
                         # cover_thickness / civil_defense
    value: str
    numeric: float | None
    evidence: str        # **证据必须带出来** —— 供人回溯复核


def _add(out: list, kind: str, value: str, evidence: str,
         numeric: float | None = None) -> None:
    out.append(SpecParameter(kind=kind, value=value, numeric=numeric,
                             evidence=evidence.strip()[:120]))


def extract_spec_parameters(lines: list[str] | None) -> list[SpecParameter]:
    """说明文字 → 标准参数列表（**判不出就不猜**）。"""
    out: list[SpecParameter] = []
    for raw in lines or []:
        text = str(raw or "")
        if not text.strip():
            continue
        for match in _CONCRETE_RE.finditer(text):
            _add(out, "concrete_grade", match.group(0), text)
        for match in _REBAR_RE.finditer(text):
            _add(out, "rebar_grade", match.group(1).replace(" ", ""), text)
        for match in _SEISMIC_RE.finditer(text):
            _add(out, "seismic_level", match.group(1), text)
        for match in _COVER_RE.finditer(text):
            value = float(match.group(1))
            if MIN_COVER_MM <= value <= MAX_COVER_MM:
                _add(out, "cover_thickness", f"{match.group(1)}mm", text, value)
        for match in _DEFENSE_RE.finditer(text):
            _add(out, "civil_defense", match.group(1).replace(" ", ""), text)
    return out
