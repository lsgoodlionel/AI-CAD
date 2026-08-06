"""识图规则内置单测(GB/T 50001)。

**为什么要有这个模块**:国标条款此前散落在各识别器的 docstring 里,
同一条规则(如「I、O、Z 不得用作轴线编号」)在两处各写一遍,改一处就会漂移。
这里做**单一来源**:常量只有一份,且每条规则附带它的**条款号与实测依据**。

**它必须是可执行的**,不能是文档堆砌——所以核心是 `validate_axis_labels`:
按 §8.0.3/§8.0.4/§8.0.5/§8.0.6 校验推导出的轴号序列,把违规如实列出来。
"""
import pytest

from core.model3d.drawing_conventions import (
    AXIS_LETTERS, CLAUSES, FORBIDDEN_AXIS_LETTERS, LABEL_CIRCLE_DIAMETER_MM,
    parse_axis_label, purpose_of_line_type, validate_axis_labels,
)


# ── 单一来源 ──────────────────────────────────────────────────

def test_forbidden_letters_are_the_ones_the_standard_names():
    """§8.0.4:I、O、Z 不得用作轴线编号(与 1、0、2 易混)。"""
    assert FORBIDDEN_AXIS_LETTERS == {"I", "O", "Z"}
    assert not (FORBIDDEN_AXIS_LETTERS & set(AXIS_LETTERS))


def test_axis_letters_start_at_a_and_skip_the_forbidden_ones():
    assert AXIS_LETTERS[:9] == "ABCDEFGHJ"
    assert "I" not in AXIS_LETTERS and "O" not in AXIS_LETTERS


def test_circle_diameter_range_matches_the_standard():
    """§8.0.2:轴号圆直径 8~10mm。"""
    assert LABEL_CIRCLE_DIAMETER_MM == (8.0, 10.0)


def test_line_type_purposes_come_from_the_standard_table():
    """§4.0.2 线型表:单点长画线=轴线,双点长画线=假想轮廓线。"""
    assert purpose_of_line_type("dash_dot") == "轴线"
    assert purpose_of_line_type("dash_dot_dot") == "外轮廓/用地界线"
    assert purpose_of_line_type("nonsense") == "未知"


def test_existing_modules_share_the_same_constants():
    """常量必须只有一份 —— 两处各写一遍,改一处就会漂移。"""
    from core.model3d.axis_label_circle import STANDARD_DIAMETER_MM
    from services.axis_label_sequence import ALPHA_AXIS

    assert STANDARD_DIAMETER_MM == LABEL_CIRCLE_DIAMETER_MM
    assert ALPHA_AXIS == AXIS_LETTERS


# ── 轴号语法(§8.0.3 / §8.0.5 / §8.0.6)────────────────────────

def test_parses_a_plain_numeric_label():
    got = parse_axis_label("12")
    assert got == {"zone": None, "value": "12", "kind": "numeric",
                   "additional": None}


def test_parses_a_zoned_letter_label():
    """§8.0.5 分区编号:「分区号-轴线号」。"""
    got = parse_axis_label("2-K")
    assert got["zone"] == "2" and got["value"] == "K"
    assert got["kind"] == "alpha"


def test_parses_a_fraction_additional_label():
    """§8.0.6 附加轴线用分数式:分子=附加序号,分母=前一轴线号。"""
    got = parse_axis_label("2-1/K")
    assert got["zone"] == "2"
    assert got["additional"] == {"index": 1, "after": "K"}


def test_parses_a_fraction_over_a_numeric_axis():
    got = parse_axis_label("2-1/11")
    assert got["additional"] == {"index": 1, "after": "11"}


def test_rejects_a_forbidden_letter():
    assert parse_axis_label("1-O") is None
    assert parse_axis_label("1-I") is None


def test_rejects_garbage():
    assert parse_axis_label("") is None
    assert parse_axis_label("甲-1") is None


# ── 序列校验(I-2.4)──────────────────────────────────────────

def test_a_clean_numeric_sequence_has_no_violations():
    labels = [f"1-{i}" for i in range(1, 25)]
    assert validate_axis_labels(labels, kind="numeric") == []


def test_a_clean_letter_sequence_has_no_violations():
    labels = ["1-A", "1-B", "1-C", "1-D", "1-E", "1-F", "1-G", "1-H",
              "1-J", "1-K", "1-L", "1-M", "1-N", "1-P", "1-Q"]
    assert validate_axis_labels(labels, kind="alpha") == []


def test_flags_a_gap_in_the_sequence():
    """§8.0.3「依次注写」——缺号说明漏检了一条轴线。"""
    violations = validate_axis_labels(["1-1", "1-2", "1-4"], kind="numeric")
    assert any(v["rule"] == "8.0.3" for v in violations)


def test_flags_a_duplicate_label():
    """重号会让两条轴线共用一个身份,跨图对齐必错。"""
    violations = validate_axis_labels(["1-1", "1-2", "1-2"], kind="numeric")
    assert any(v["rule"] == "duplicate" for v in violations)


def test_flags_mixed_zones_in_one_sequence():
    """§8.0.5:一条带内的轴号必须同属一个分区。"""
    violations = validate_axis_labels(["1-1", "2-2", "1-3"], kind="numeric")
    assert any(v["rule"] == "8.0.5" for v in violations)


def test_flags_a_letter_in_a_numeric_sequence():
    """§8.0.3:横向编号用数字、竖向用字母,不能混。"""
    violations = validate_axis_labels(["1-1", "1-A"], kind="numeric")
    assert any(v["rule"] == "8.0.3" for v in violations)


def test_letter_sequence_skipping_i_is_not_a_gap():
    """A→…→H→J 跳过 I 是**合规**的,不能报成缺号。"""
    assert validate_axis_labels(["1-G", "1-H", "1-J"], kind="alpha") == []


def test_additional_axes_are_not_part_of_the_sequence():
    """§8.0.6 附加轴线不占主序号,夹在中间也不该报缺号。"""
    labels = ["1-1", "1-1/1", "1-2"]
    assert validate_axis_labels(labels, kind="numeric") == []


def test_violations_carry_the_clause_text():
    """报出来的违规要能直接告诉人是哪一条 —— 否则没法处理。"""
    v = validate_axis_labels(["1-1", "1-3"], kind="numeric")[0]
    assert v["rule"] in CLAUSES
    assert CLAUSES[v["rule"]]["text"]


def test_validate_on_empty():
    assert validate_axis_labels([], kind="numeric") == []


def test_unparsable_label_is_reported_not_swallowed():
    violations = validate_axis_labels(["1-1", "??"], kind="numeric")
    assert any(v["rule"] == "unparsable" for v in violations)


# ── 条款登记表 ────────────────────────────────────────────────

def test_every_clause_records_where_it_is_applied():
    """规则不能只是抄下来 —— 必须写明它在哪个模块生效。"""
    for clause_id, clause in CLAUSES.items():
        assert clause["text"], clause_id
        assert clause["applied_in"], clause_id


def test_the_locating_axis_chapter_is_covered():
    """第 8 章(定位轴线)七条都要在册,缺哪条一目了然。"""
    for n in range(1, 8):
        assert f"8.0.{n}" in CLAUSES


def test_clauses_carry_measured_evidence_where_we_have_it():
    """有实测支撑的条款要带数据 —— 这是它区别于文档的地方。"""
    assert CLAUSES["8.0.2"]["evidence"]
    assert "28.0" in CLAUSES["8.0.2"]["evidence"] or "9.88" in CLAUSES["8.0.2"]["evidence"]
