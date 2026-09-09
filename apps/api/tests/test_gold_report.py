"""金标准常设报表与结构校验。

**为什么要有它**：`data/model3d/gold/` 是这个项目现在唯一可信的度量来源，
而它此前只能靠一次性脚本读。一次性脚本的口径不留痕，于是同一个「柱」
被算出过 0.59 / 0.22 / 68% / 22% 四个数字。报表把口径固定成代码：

1. **分组必须拆开** —— 对照组与被闸删掉组不能与主组混算
   （`columns_final` 混算是 60%，真实的 kept 组只有 12%）；
2. **图纸级两极分化**是全部数据里最强的信号，做成常设指标；
3. **误检标签归一** —— 同一件事有 `wall`/`墙`/`no_wall` 三种写法。

测试用**合成单元**验口径，另有几例在**真实金标准文件**上锁住关键数字，
数字变了就该有人来解释为什么。
"""
from __future__ import annotations

import json

import pytest

from core.model3d.gold.report import (
    build_report, class_reports, polarization, render_markdown,
    taxonomy_summary)
from core.model3d.gold.schema_check import (
    RAW_SHEETS, check_file, check_gold_dir, gold_dir, load_gold_files)
from core.model3d.gold.taxonomy import BUCKETS, canonical_what, normalize_table


def _file(name, cls, units):
    """造一个标准结构的金标准文件（内存形态）。"""
    return {"name": name,
            "data": {"version": 1, "object_classes": [cls], "units": units}}


def _unit(uid, cls, verdicts, *, source=None, criteria="CRITERIA.md#columns"):
    return {"unit": uid, "source": source or {}, "classes": {cls: {
        "method": "verdicts", "verdicts": verdicts, "confidence": 1.0,
        "verified_by": ["gpt"], "criteria": criteria}}}


def _v(ref, ok, what=""):
    d = {"ref": ref, "ok": ok}
    if not ok:
        d["what"] = what or "wall"
    return d


# ---------------------------------------------------------------- 结构校验

@pytest.mark.unit
def test_standard_file_passes_schema_check():
    f = _file("demo_v1.json", "demo", [_unit("D1", "demo", [_v("A", True)])])
    assert check_file(f["name"], f["data"]) == []


@pytest.mark.unit
def test_missing_units_key_is_reported_as_non_standard():
    """没有 `units` 的文件汇总脚本要特判 —— 特判就是缺陷，必须报出来。"""
    issues = check_file("weird_v1.json", {"results": [{"id": "P1-01"}]})
    assert any(i["code"] == "non_standard_shape" for i in issues)


@pytest.mark.unit
def test_declared_raw_sheet_is_exempt_with_a_reason():
    """原始判读接触表**不是**金标准单元文件，豁免但必须自己声明来路。

    豁免靠文件自带的 `kind` 字段，不靠脚本里写死文件名 ——
    写死的清单会和数据漂移。
    """
    issues = check_file("patch_verdicts_v1.json", {
        "kind": "raw_judgement_sheet",
        "materialized_in": "verdicts_v1.json",
        "results": [{"id": "P1-01", "is_column": "yes"}]})
    assert issues == []


@pytest.mark.unit
def test_raw_sheet_without_kind_is_still_reported():
    """只在校验里放行文件名、而文件自己不声明，等于把豁免藏起来。"""
    issues = check_file("patch_verdicts_v1.json", {"results": []})
    assert any(i["code"] == "non_standard_shape" for i in issues)


@pytest.mark.unit
def test_verdict_class_without_criteria_is_reported():
    """判据不回指，数字就不可比 —— 这正是 0.59 vs 0.22 的病根。"""
    issues = check_file("demo_v1.json", {
        "version": 1, "object_classes": ["demo"],
        "units": [_unit("D1", "demo", [_v("A", True)], criteria=None)]})
    assert any(i["code"] == "criteria_missing" for i in issues)


@pytest.mark.unit
def test_criteria_may_declare_itself_unfixed():
    """前 CRITERIA 时代的批次补不出判据，允许如实标注「未固化」。

    标注之后它仍然**不可与后续批次比较**，但至少这件事写在数据里，
    而不是留给读者猜。
    """
    issues = check_file("demo_v1.json", {
        "version": 1, "object_classes": ["demo"],
        "units": [_unit("D1", "demo", [_v("A", True)],
                        criteria="UNFIXED: 本批判据未固化，与后续批次不可比")]})
    assert [i for i in issues if i["code"] == "criteria_missing"] == []


@pytest.mark.unit
def test_dangling_criteria_anchor_is_reported():
    issues = check_file("demo_v1.json", {
        "version": 1, "object_classes": ["demo"],
        "units": [_unit("D1", "demo", [_v("A", True)],
                        criteria="CRITERIA.md#不存在的小节")],
    }, criteria_sections={"columns", "walls"})
    assert any(i["code"] == "criteria_dangling" for i in issues)


@pytest.mark.unit
def test_object_classes_header_must_match_the_units():
    issues = check_file("demo_v1.json", {
        "version": 1, "object_classes": ["something_else"],
        "units": [_unit("D1", "demo", [_v("A", True)])]})
    assert any(i["code"] == "object_classes_mismatch" for i in issues)


@pytest.mark.unit
def test_unparsable_unit_surfaces_the_schema_error():
    """`ok=False` 却不写 `what`：schema 已经会炸，校验要把它转成问题条目。"""
    issues = check_file("demo_v1.json", {
        "version": 1, "object_classes": ["demo"],
        "units": [{"unit": "D1", "source": {}, "classes": {"demo": {
            "method": "verdicts", "confidence": 1.0, "verified_by": ["gpt"],
            "criteria": "CRITERIA.md#columns",
            "verdicts": [{"ref": "A", "ok": False}]}}}]})
    assert any(i["code"] == "unparsable" for i in issues)


# ------------------------------------------------------------ 分类汇总口径

@pytest.mark.unit
def test_control_and_dropped_groups_are_split_out_of_the_main_rate():
    """混算会把 12% 说成 60%。这条正是 `columns_final` 的真实情形。"""
    units = [
        _unit("C2-kept", "cf", [_v(f"k{i}", i < 3) for i in range(26)],
              source={"group": "kept"}),
        _unit("C2-blank_control", "cf", [_v(f"b{i}", True) for i in range(10)],
              source={"group": "blank_control"}),
        _unit("C2-dropped_by_gate", "cf", [_v(f"d{i}", True) for i in range(22)],
              source={"group": "dropped_by_gate"}),
    ]
    rep = class_reports([_file("cf_v1.json", "cf", units)])[0]
    assert rep.verdicts == 58
    assert rep.main_verdicts == 26
    assert round(rep.main_rate, 2) == 0.12
    assert round(rep.overall_rate, 2) == 0.60      # 混算的那个数，留作对照
    assert {g.name for g in rep.groups if g.auxiliary} == {
        "blank_control", "dropped_by_gate"}


@pytest.mark.unit
def test_non_verdict_classes_report_no_rate_rather_than_zero():
    """`count`/`text`/`fields`/`instances` 记的是计数与字段，没有精确率。

    报 0% 会让读者以为「测出来是零」，必须报「不适用」。
    """
    units = [{"unit": "T1", "source": {}, "classes": {"drawing_title": {
        "method": "text", "text": "一层平面图", "confidence": 1.0,
        "verified_by": ["ocr_cross_check"]}}}]
    rep = class_reports([_file("t_v1.json", "drawing_title", units)])[0]
    assert rep.has_rate is False
    assert rep.main_rate is None
    assert rep.method == "text"


# ---------------------------------------------------------- 图纸级两极分化

@pytest.mark.unit
def test_polarization_groups_by_drawing_not_by_tile():
    """两极分化是**图纸级**指标：同一张图的多个瓦片必须并成一张图。

    盘点文档按 `unit`（瓦片）分组算出 82%，而按图纸分组只有 50% ——
    差别不在数据，在口径。
    """
    units = [
        _unit("VD-t1", "columns", [_v("a", True), _v("b", True)],
              source={"drawing_id": "D1", "tile": "t1"}),
        _unit("VD-t2", "columns", [_v("c", False), _v("d", False)],
              source={"drawing_id": "D1", "tile": "t2"}),
        _unit("VD-t3", "columns", [_v("e", True), _v("f", True)],
              source={"drawing_id": "D2", "tile": "t3"}),
    ]
    files = [_file("v_v1.json", "columns", units)]
    by_drawing = polarization(files)["columns"]
    assert by_drawing.drawings == 2
    assert (by_drawing.all_ok, by_drawing.all_bad, by_drawing.mixed) == (1, 0, 1)

    by_unit = polarization(files, by="unit")["columns"]
    assert by_unit.drawings == 3
    assert (by_unit.all_ok, by_unit.all_bad) == (2, 1)


@pytest.mark.unit
def test_polarization_skips_control_and_dropped_units():
    units = [
        _unit("X-kept", "cf", [_v("a", False), _v("b", False)],
              source={"drawing_id": "D1", "group": "kept"}),
        _unit("X-blank_control", "cf", [_v("c", True), _v("d", True)],
              source={"drawing_id": "D2", "group": "blank_control"}),
    ]
    pol = polarization([_file("cf_v1.json", "cf", units)])["cf"]
    assert pol.drawings == 1 and pol.all_bad == 1


@pytest.mark.unit
def test_polarization_ignores_drawings_with_a_single_verdict():
    """一格的图天然「全对」或「全错」，算进去等于自证两极分化。"""
    units = [
        _unit("A", "cf", [_v("a", True)], source={"drawing_id": "D1"}),
        _unit("B", "cf", [_v("b", True), _v("c", False)],
              source={"drawing_id": "D2"}),
    ]
    pol = polarization([_file("cf_v1.json", "cf", units)])["cf"]
    assert pol.drawings == 1 and pol.mixed == 1


# ---------------------------------------------------------------- 误检归一

@pytest.mark.unit
def test_synonym_variants_normalize_to_one_label():
    assert canonical_what("wall") == canonical_what("墙") == canonical_what("no_wall")
    assert canonical_what("nothing") == canonical_what("空白") == canonical_what("no_empty")
    assert canonical_what("beam_or_grid") == canonical_what("beam")


@pytest.mark.unit
def test_every_mapped_label_lands_in_a_declared_bucket():
    for _raw, (label, bucket) in normalize_table().items():
        assert bucket in BUCKETS, f"{label} 落在未声明的桶 {bucket}"


@pytest.mark.unit
def test_unmapped_label_is_surfaced_not_silently_dropped():
    """新批次会带来新写法。未登记的必须被点名，否则归一表会悄悄失真。"""
    units = [_unit("U", "cf", [_v("a", False, "某种前所未见的东西")])]
    summary = taxonomy_summary([_file("cf_v1.json", "cf", units)])
    assert "某种前所未见的东西" in summary.unmapped


@pytest.mark.unit
def test_taxonomy_records_which_batch_each_variant_came_from():
    """同义变体来自不同批次 —— 来源必须留痕，否则没法追为什么会有两种写法。"""
    units = [_unit("U", "cf", [_v("a", False, "墙")])]
    summary = taxonomy_summary([_file("cf_v1.json", "cf", units)])
    assert summary.sources["墙"] == {"cf_v1.json"}


# ------------------------------------------------------- 真实金标准上的锁

@pytest.fixture(scope="module")
def real_files():
    files = load_gold_files()
    if not files:
        pytest.skip("金标准数据目录不可达")
    return files


@pytest.mark.unit
def test_real_gold_dir_passes_schema_check(real_files):
    """整理之后，全目录应当 0 个 error 级问题。"""
    issues = [i for i in check_gold_dir() if i["level"] == "error"]
    assert issues == [], issues


@pytest.mark.unit
def test_real_totals_match_the_blueprint(real_files):
    """1096 条裁决 —— 盘点文档的总数，报表必须对得上。"""
    rep = build_report(real_files)
    assert rep.total_verdicts == 1096
    assert rep.total_files == 22


@pytest.mark.unit
def test_real_class_rates_match_the_blueprint(real_files):
    """逐类真阳率对齐盘点表。对不上说明有一方错了，要查清是哪一方。

    锁的是**分子分母**而不是百分比：`axis_grid_presence` 真实是 74/80 = 92.5%，
    盘点文档写的 92% 是 `%.0f` 的向偶数舍入 —— 锁百分比会把舍入方式也锁进来。
    """
    got = {r.object_class: (r.units, r.verdicts, r.main_ok, r.main_verdicts)
           for r in class_reports(real_files) if r.has_rate}
    assert got["axis_grid_presence"] == (1, 80, 74, 80)      # 92.5%
    assert got["floor_assignment"] == (2, 80, 61, 80)        # 76%
    assert got["has_discipline"] == (2, 90, 67, 90)          # 74%
    assert got["columns"] == (109, 120, 71, 120)             # 59%
    assert got["beams"] == (15, 50, 28, 50)                  # 56%
    assert got["walls"] == (13, 47, 33, 47)                  # 70%
    assert got["building_unit"] == (2, 80, 30, 80)           # 38%
    assert got["drawing_scale"] == (4, 56, 17, 56)           # 30%
    assert got["column_outline"] == (2, 60, 13, 60)          # 22%
    assert got["equipment"] == (20, 60, 10, 60)              # 17%
    assert got["slabs"] == (21, 50, 5, 50)                   # 10%
    assert got["pipes"] == (20, 58, 0, 58)                   # 0%
    # 复测批：主组（去掉空白对照）才是可比的数
    assert got["beams_retest"][2:] == (21, 34)               # 62%
    assert got["walls_retest"][2:] == (21, 37)               # 57%
    assert got["columns_final"][2:] == (3, 26)               # 12%（混算是 60%）


@pytest.mark.unit
def test_blueprint_table_is_missing_the_walls_v1_row(real_files):
    """盘点文档的 16 行表加起来只有 1049 条，缺了 `walls`(47 条)。

    这条测试把「文档漏了一行」这件事**钉在代码里** —— 总数 1096 是对的，
    表是不全的。补表之前，任何按那张表求和的结论都会少 47 条。
    """
    rated = [r for r in class_reports(real_files) if r.has_rate]
    assert sum(r.verdicts for r in rated) == 1096
    assert sum(r.verdicts for r in rated if r.object_class != "walls") == 1049


@pytest.mark.unit
def test_real_misdetection_total(real_files):
    """559 条误检 —— 与盘点文档一致。"""
    summary = taxonomy_summary(real_files)
    assert summary.total == 559
    assert summary.unmapped == {}, f"未登记的 what 写法：{summary.unmapped}"


@pytest.mark.unit
def test_real_polarization_by_drawing(real_files):
    """按**图纸**分组的两极分化。`columns` 与盘点文档不同，原因见模块文档。"""
    pol = polarization(real_files)
    assert (pol["pipes"].drawings, pol["pipes"].polarized) == (19, 19)
    assert (pol["equipment"].drawings, pol["equipment"].polarized) == (20, 17)
    assert (pol["beams"].drawings, pol["beams"].polarized) == (14, 11)
    assert (pol["walls"].drawings, pol["walls"].polarized) == (13, 9)
    assert (pol["slabs"].drawings, pol["slabs"].polarized) == (8, 5)
    # 盘点文档按瓦片算得 11 张 / 82%；按图纸算是 20 张 / 35%
    assert (pol["columns"].drawings, pol["columns"].polarized) == (20, 7)
    assert (polarization(real_files, by="unit")["columns"].drawings) == 11


@pytest.mark.unit
def test_criteria_coverage_is_reported(real_files):
    rep = build_report(real_files)
    assert 0 < rep.criteria_coverage <= 1.0
    assert rep.classes_with_criteria + rep.classes_without_criteria == len(
        class_reports(real_files))


@pytest.mark.unit
def test_markdown_report_renders_the_standing_metrics(real_files):
    md = render_markdown(build_report(real_files))
    for heading in ("分类汇总", "图纸级两极分化", "误检归一", "判据回指"):
        assert heading in md
    assert "1096" in md


@pytest.mark.unit
def test_patch_sheet_is_declared_and_not_double_counted(real_files):
    """`patch_verdicts_v1.json` 的 120 行已逐条并入 `verdicts_v1.json`。

    把它当成金标准文件再数一遍，总数会从 1096 虚增到 1216。
    """
    raw = json.loads((gold_dir() / "patch_verdicts_v1.json").read_text("utf-8"))
    assert raw["kind"] == "raw_judgement_sheet"
    assert raw["materialized_in"] == "verdicts_v1.json"
    assert "patch_verdicts_v1.json" in RAW_SHEETS
    assert len(raw["results"]) == 120
    assert build_report(real_files).total_verdicts == 1096
