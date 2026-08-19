"""设计说明参数提取 —— **方向调整后的正确解法**。

**三条路径都没走通**（本轮实测）：x 聚类分栏 → XY-cut 分块 →
按块重组，条文数始终停在 8 条、平均 4000+ 字的拼接。
根本困难是 OCR 输出乱序 + 说明版面不规则（分块套多栏），
需要专门的文档版面模型。

**但读设计说明的目的是拿参数，不是复原条文全文。**
参数有固定的书写规范（GB 50010 强度等级、GB 50011 抗震等级），
可以直接从行里提 —— **完全不需要版面恢复**。

实测两工程同构：

| 参数 | 大歌剧院 | 轨道交通 |
|---|---:|---:|
| 混凝土强度 | 79 | 48 |
| 钢筋级别 | 10 | 15 |
| 保护层厚度 | 10 | 3 |
| 人防等级 | 10 | — |
| 抗震等级 | 3 | 1 |
"""
from __future__ import annotations

import pytest

from core.model3d.spec_parameters import extract_spec_parameters


def _kinds(items):
    return {i.kind for i in items}


@pytest.mark.unit
def test_concrete_grade():
    """GB 50010 混凝土强度等级 C15~C80，**5 的倍数**。"""
    got = extract_spec_parameters(["垫层采用C15素混凝土", "梁板柱均为C30"])
    assert _kinds(got) == {"concrete_grade"}
    assert {i.value for i in got} == {"C15", "C30"}


@pytest.mark.unit
def test_non_grade_c_numbers_rejected():
    """**不得误收** —— `C12@200` 是钢筋规格、`C65H` 是断路器型号。"""
    got = extract_spec_parameters(["箍筋C12@200", "断路器C65H-C16A/2P"])
    assert not [i for i in got if i.kind == "concrete_grade"]


@pytest.mark.unit
def test_rebar_grade():
    """GB 1499 钢筋牌号。"""
    got = extract_spec_parameters(["纵筋采用HRB400，箍筋HPB300"])
    assert {i.value for i in got if i.kind == "rebar_grade"} == {"HRB400", "HPB300"}


@pytest.mark.unit
def test_seismic_level():
    """GB 50011 抗震等级（含特一级）。"""
    got = extract_spec_parameters(["本工程框架抗震等级为二级",
                                   "剪力墙抗震等级为特一级"])
    assert {i.value for i in got if i.kind == "seismic_level"} == {"二级", "特一级"}


@pytest.mark.unit
def test_cover_thickness_with_unit():
    """保护层厚度带数值与单位。"""
    got = extract_spec_parameters(["梁的保护层厚度为25mm",
                                   "底板保护层厚度不应小于50mm"])
    covers = [i for i in got if i.kind == "cover_thickness"]
    assert {i.numeric for i in covers} == {25.0, 50.0}


@pytest.mark.unit
def test_implausible_cover_rejected():
    """**厚度要合理** —— 保护层不会是 5mm 或 500mm。"""
    got = extract_spec_parameters(["保护层厚度为500mm", "保护层厚度为3mm"])
    assert not [i for i in got if i.kind == "cover_thickness"]


@pytest.mark.unit
def test_civil_defense_level():
    """人防抗力级别（核 X 级 / 常 X 级）—— 实测大歌剧院 10 处。"""
    got = extract_spec_parameters(["地下室按核6级、常6级设计"])
    assert {i.value for i in got if i.kind == "civil_defense"} == {"核6级", "常6级"}


@pytest.mark.unit
def test_evidence_is_carried():
    """**证据必须带出来** —— 参数要能回溯到原句，供人复核。"""
    got = extract_spec_parameters(["本工程框架抗震等级为二级。"])
    assert got[0].evidence.startswith("本工程框架抗震等级")


@pytest.mark.unit
def test_empty_and_noise():
    assert extract_spec_parameters([]) == []
    assert extract_spec_parameters(None) == []
    assert extract_spec_parameters(["一层平面图", "3.600"]) == []
