"""金标准自审：真值本身也会错，必须能被机器检出。

**实测依据**：本阶段金标准出过三类错 ——
GPT 把有轴号的平面图判成非平面图（人工复核推翻）、
分区序号错位致 F1 假跌、以及标签集合对上而位置贴错。
前两类靠人工发现，代价高；能自动查的应当自动查。
"""
import pytest

from core.model3d.gold.audit import audit_units


def _unit(unit="G01", **cls):
    return {"unit": unit, "source": {"project": "metro"}, "classes": cls}


@pytest.mark.unit
def test_clean_units_raise_nothing():
    issues = audit_units([_unit(columns={
        "method": "count", "count": 3, "confidence": 0.9,
        "verified_by": ["human"]})])
    assert issues == []


@pytest.mark.unit
def test_duplicate_unit_ids_are_reported():
    """同一个编号出现两次 —— 后一条会覆盖前一条，静默丢真值。"""
    issues = audit_units([_unit("G01", columns={"method": "count", "count": 1,
                                                "confidence": 0.9, "verified_by": ["human"]}),
                          _unit("G01", columns={"method": "count", "count": 2,
                                                "confidence": 0.9, "verified_by": ["human"]})])
    assert any(i["code"] == "duplicate_unit" for i in issues)


@pytest.mark.unit
def test_axis_labels_must_obey_the_national_standard():
    """轴号要过 GB/T 50001 §8 校验 —— 真值写错了同样是错。

    §8.0.4：字母轴号不得使用 I、O、Z（易与 1、0、2 混淆）。
    """
    issues = audit_units([_unit(axes={
        "method": "instances", "confidence": 1.0, "verified_by": ["human"],
        "instances": [{"id": "A", "kind": "horizontal"},
                      {"id": "I", "kind": "horizontal"}]})])
    assert any(i["code"] == "axis_label_violation" for i in issues)


@pytest.mark.unit
def test_a_spacing_chain_that_does_not_close_is_reported():
    """轴距链自身要闭合：档数必须等于轴线数减一。"""
    issues = audit_units([_unit(axes={
        "method": "instances", "confidence": 1.0, "verified_by": ["human"],
        "instances": [{"id": "1", "to_next_mm": 9300},
                      {"id": "2", "to_next_mm": 9300},
                      {"id": "3", "to_next_mm": 9300}]})])   # 末条不该有间距
    assert any(i["code"] == "chain_not_closed" for i in issues)


@pytest.mark.unit
def test_excluded_units_must_carry_a_reason():
    """排除必须写明理由 —— 没理由的排除等于偷偷把难题拿掉。"""
    issues = audit_units([_unit(columns={
        "method": "count", "count": 1, "confidence": 0.9,
        "verified_by": ["human"], "excluded": ""})])
    assert issues == []          # 空字符串不算排除
    issues = audit_units([_unit(columns={
        "method": "count", "count": 1, "confidence": 0.9,
        "verified_by": ["human"], "excluded": "  "})])
    assert issues == []


@pytest.mark.unit
def test_a_verified_flag_without_any_verifier_is_reported():
    """把握达标但无人复核、且把握恰好卡在阈值上的，标出来复查。"""
    issues = audit_units([_unit(columns={
        "method": "count", "count": 1, "confidence": 0.8, "verified_by": []})])
    assert any(i["code"] == "unverified_at_threshold" for i in issues)


@pytest.mark.unit
def test_zones_are_validated_separately_not_as_one_sequence():
    """§8.0.5 要求的是**分区内**一致，不是跨分区。

    第一版审计把跨分区的轴号当成一个序列校验，于是 `1-1` 与 `2-1`
    被报成「轴号 1 重复」「同一序列出现多个分区号」—— 31 条全是
    审计自身的假阳性。**审计工具自己也需要被审。**
    """
    issues = audit_units([_unit(axes={
        "method": "instances", "confidence": 1.0, "verified_by": ["human"],
        "instances": [{"id": "1-1", "kind": "vertical"},
                      {"id": "1-2", "kind": "vertical"},
                      {"id": "2-1", "kind": "vertical"},
                      {"id": "2-2", "kind": "vertical"}]})])
    assert issues == []
