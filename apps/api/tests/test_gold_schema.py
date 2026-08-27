"""全套金标准的记录格式。

**为什么用轴号而不是像素坐标做实体身份**：`1×A` 这样的轴线交点是
图纸自带的寻址方式 —— 人能核对、跨比例稳定、不受坐标系是否统一影响。
而当前实测跨图位置对应率只有 1.6~4.7%（坐标系尚未统一），
用坐标做真值等于把一个未解决的问题写进标尺里。
"""
import pytest

from core.model3d.gold.schema import GoldUnit, ObjectClass, parse_unit


def _unit(**over):
    base = {
        "unit": "G01",
        "source": {"project": "metro", "drawing_id": "7c73545a", "tile": "t0015"},
        "classes": {
            "columns": {"method": "count", "count": 8, "confidence": 0.9,
                        "verified_by": ["gpt", "human"]},
        },
    }
    base.update(over)
    return base


@pytest.mark.unit
def test_a_minimal_unit_parses():
    unit = parse_unit(_unit())
    assert isinstance(unit, GoldUnit)
    assert unit.unit == "G01"
    assert unit.classes["columns"].count == 8


@pytest.mark.unit
def test_instances_carry_axis_identity_not_pixels():
    """实体级真值用轴号定身份，尺寸用毫米。"""
    unit = parse_unit(_unit(classes={"columns": {
        "method": "instances",
        "instances": [{"id": "1×A", "size_mm": [600, 600]},
                      {"id": "1×B", "size_mm": [600, 800]}],
        "confidence": 0.9, "verified_by": ["human"]}}))
    cols = unit.classes["columns"]
    assert cols.count == 2                      # 实体级自动给出计数
    assert cols.instances[0].id == "1×A"
    assert cols.instances[1].size_mm == (600, 800)


@pytest.mark.unit
def test_text_class_holds_the_string():
    """图名/总说明这类真值是文本。"""
    unit = parse_unit(_unit(classes={"drawing_title": {
        "method": "text", "text": "一层结构平面图", "confidence": 1.0,
        "verified_by": ["human"]}}))
    assert unit.classes["drawing_title"].text == "一层结构平面图"


@pytest.mark.unit
def test_fields_class_holds_a_mapping():
    """图框标题栏是若干字段。"""
    unit = parse_unit(_unit(classes={"title_block": {
        "method": "fields",
        "fields": {"scale": "1:100", "drawing_no": "S-01"},
        "confidence": 1.0, "verified_by": ["human"]}}))
    assert unit.classes["title_block"].fields["scale"] == "1:100"


@pytest.mark.unit
def test_excluded_class_is_kept_but_flagged():
    """排除的条目**保留在档**并带理由——删掉就看不见协议在起作用。"""
    unit = parse_unit(_unit(classes={"columns": {
        "method": "count", "count": 4, "confidence": 0.72,
        "verified_by": ["gpt"], "excluded": "埋件图",
        "note": "预埋钢板不是柱"}}))
    cls = unit.classes["columns"]
    assert cls.excluded == "埋件图"
    assert not cls.counts_toward_metrics


@pytest.mark.unit
def test_unverified_class_does_not_count_toward_metrics():
    """只有 GPT 一方、且把握低于阈值的，不进指标。"""
    unit = parse_unit(_unit(classes={"columns": {
        "method": "count", "count": 5, "confidence": 0.45,
        "verified_by": ["gpt"]}}))
    assert not unit.classes["columns"].counts_toward_metrics


@pytest.mark.unit
def test_unknown_method_is_rejected_loudly():
    """判不出格式就报错，不静默降级。"""
    with pytest.raises(ValueError, match="method"):
        parse_unit(_unit(classes={"columns": {"method": "guess", "count": 1}}))


@pytest.mark.unit
def test_instance_ids_must_be_unique():
    """同一轴号交点上不能有两根柱——重复即记录有误。"""
    with pytest.raises(ValueError, match="重复"):
        parse_unit(_unit(classes={"columns": {
            "method": "instances",
            "instances": [{"id": "1×A"}, {"id": "1×A"}],
            "confidence": 0.9, "verified_by": ["human"]}}))


# --- 身份必须带分区：裸轴号在过半图纸上会撞车 -------------------------

@pytest.mark.unit
def test_identity_is_qualified_by_zone():
    """同一张图的两个分区都有「1 轴」，那是两条不同的轴线。

    **实测**（两个工程共 5.3 万条识别轴号）：

    | 身份口径 | 大歌剧院重复率 | 轨道交通重复率 |
    |---|---|---|
    | 裸轴号 | **31%** | **37%** |
    | **(分区, 轴号)** | **0%** | **0%** |

    存在重复轴号的图占 61% / 53%。裸轴号做身份，过半图纸上会撞车 ——
    GB/T 50001 §8.0.5 的分区编号本就规定轴号形如「分区号-轴线号」。
    """
    unit = parse_unit(_unit(classes={"axes": {
        "method": "instances",
        "instances": [{"id": "1", "zone": "1"}, {"id": "1", "zone": "2"}],
        "confidence": 1.0, "verified_by": ["human"]}}))
    axes = unit.classes["axes"]
    assert axes.count == 2
    assert axes.instances[0].key == "1·1"
    assert axes.instances[1].key == "2·1"


@pytest.mark.unit
def test_same_label_in_the_same_zone_is_still_a_duplicate():
    """同一分区里的同一轴号才算重复。"""
    with pytest.raises(ValueError, match="重复"):
        parse_unit(_unit(classes={"axes": {
            "method": "instances",
            "instances": [{"id": "1", "zone": "1"}, {"id": "1", "zone": "1"}],
            "confidence": 1.0, "verified_by": ["human"]}}))


@pytest.mark.unit
def test_no_zone_falls_back_to_the_bare_label():
    """轴号本身已含分区前缀（`1-A`）时不必再写 zone。"""
    unit = parse_unit(_unit(classes={"axes": {
        "method": "instances", "instances": [{"id": "1-A"}],
        "confidence": 1.0, "verified_by": ["human"]}}))
    assert unit.classes["axes"].instances[0].key == "1-A"


# --- 第五种真值：对既有识别结果的逐项裁决 -----------------------------

@pytest.mark.unit
def test_verdicts_carry_a_ruling_per_existing_candidate():
    """`verdicts` 判的是「这个候选框对不对」，不是「这处有几根柱」。

    两者是不同的真值形态：前者以**识别结果**为单位，能直接算精确率
    并给出误检分类；后者以**图面区域**为单位，能算召回率。
    实测 120 个候选的逐项裁决（yes 71 / no 49）此前落在格式之外，
    因而不受自审、不进统一评分。
    """
    unit = parse_unit(_unit(classes={"columns": {
        "method": "verdicts",
        "verdicts": [{"ref": "P1-01", "ok": True},
                     {"ref": "P1-02", "ok": False, "what": "标高三角"}],
        "confidence": 0.9, "verified_by": ["gpt", "human"]}}))
    cls = unit.classes["columns"]
    assert cls.count == 2                     # 裁决数即候选数
    assert cls.verdicts[1].what == "标高三角"


@pytest.mark.unit
def test_a_negative_verdict_must_say_what_it_actually_is():
    """判「不是」必须说明是什么 —— 只说不是，改不了识别器。"""
    with pytest.raises(ValueError, match="what"):
        parse_unit(_unit(classes={"columns": {
            "method": "verdicts",
            "verdicts": [{"ref": "P1-02", "ok": False}],
            "confidence": 0.9, "verified_by": ["human"]}}))


@pytest.mark.unit
def test_verdict_refs_must_be_unique():
    with pytest.raises(ValueError, match="重复"):
        parse_unit(_unit(classes={"columns": {
            "method": "verdicts",
            "verdicts": [{"ref": "P1-01", "ok": True},
                         {"ref": "P1-01", "ok": True}],
            "confidence": 0.9, "verified_by": ["human"]}}))
