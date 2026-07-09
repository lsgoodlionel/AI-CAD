"""文件名解析器测试（蓝图 4.2：专业前缀 / 图号 / 版本 / 兜底各分支）"""
import pytest

from services.drawing_filename_parser import (
    parse_drawing_filename,
    parse_drawing_filename_evidence,
)


# ── 规则 1：专业前缀 ─────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize(
    "filename,expected",
    [
        ("结施-01_一层梁配筋图_B.dxf", "structure"),
        ("GS-101 基础平面图.pdf", "structure"),
        ("建施-02 二层平面图.pdf", "architecture"),
        ("JS_03.dwg", "architecture"),
        ("水施-03 给排水平面.dwg", "mep"),
        ("电施-05 配电干线图B版.pdf", "mep"),
        ("暖施-01.pdf", "mep"),
        ("机施-02.pdf", "mep"),
        ("SS-07 喷淋平面.pdf", "mep"),
        ("DS-08.dxf", "mep"),
        ("NS-12", "mep"),
        ("装施-04 吊顶大样.pdf", "decoration"),
        ("ZS-09.pdf", "decoration"),
        ("项目总说明.pdf", "general"),
    ],
)
def test_discipline_prefix_mapping(filename, expected):
    assert parse_drawing_filename(filename)["discipline"] == expected


@pytest.mark.unit
def test_lowercase_letter_prefix_is_recognized():
    assert parse_drawing_filename("gs-101.pdf")["discipline"] == "structure"


# ── 规则 2：图号 ────────────────────────────────────────────────

@pytest.mark.unit
def test_drawing_no_extracted_from_first_match():
    result = parse_drawing_filename("结施-01_一层梁配筋图_B.dxf")
    assert result["drawing_no"] == "结施-01"


@pytest.mark.unit
def test_drawing_no_with_letter_prefix():
    assert parse_drawing_filename("GS-101 基础平面图.pdf")["drawing_no"] == "GS-101"


@pytest.mark.unit
def test_drawing_no_falls_back_to_stem_when_no_match():
    result = parse_drawing_filename("项目总说明.pdf")
    assert result["drawing_no"] == "项目总说明"


# ── 规则 3：版本 ────────────────────────────────────────────────

@pytest.mark.unit
def test_version_from_underscore_suffix():
    assert parse_drawing_filename("结施-01_一层梁配筋图_B.dxf")["version"] == "B"


@pytest.mark.unit
def test_version_from_ban_marker():
    assert parse_drawing_filename("电施-05 配电干线图B版.pdf")["version"] == "B"


@pytest.mark.unit
def test_version_with_v_prefix():
    assert parse_drawing_filename("JS-102_VC.pdf")["version"] == "C"


@pytest.mark.unit
def test_version_defaults_to_a():
    assert parse_drawing_filename("GS-101 基础平面图.pdf")["version"] == "A"


# ── 规则 4：标题 ────────────────────────────────────────────────

@pytest.mark.unit
def test_title_strips_drawing_no_and_version():
    result = parse_drawing_filename("结施-01_一层梁配筋图_B.dxf")
    assert result["title"] == "一层梁配筋图"


@pytest.mark.unit
def test_title_strips_ban_version_marker():
    result = parse_drawing_filename("电施-05 配电干线图B版.pdf")
    assert result["title"] == "配电干线图"


# ── 兜底 / 结构 ─────────────────────────────────────────────────

@pytest.mark.unit
def test_empty_filename_returns_safe_defaults():
    result = parse_drawing_filename("")
    assert result == {"drawing_no": "", "discipline": "general", "title": "", "version": "A"}


@pytest.mark.unit
def test_result_contains_exactly_contract_keys():
    result = parse_drawing_filename("结施-01.pdf")
    assert set(result) == {"drawing_no", "discipline", "title", "version"}


@pytest.mark.unit
def test_parse_drawing_filename_evidence_returns_structured_fields():
    result = parse_drawing_filename_evidence("结施-01_一层梁配筋图_B.dxf")

    assert result.drawing_no.value == "结施-01"
    assert result.drawing_no.confidence == pytest.approx(0.95)
    assert result.drawing_no.span == (0, 5)
    assert result.discipline.value == "structure"
    assert result.discipline.confidence == pytest.approx(0.9)
    assert result.discipline.source == "filename"
    assert result.title.value == "一层梁配筋图"
    assert result.title.confidence == pytest.approx(0.8)
    assert result.version.value == "B"
    assert result.version.confidence == pytest.approx(0.9)


@pytest.mark.unit
def test_parse_drawing_filename_evidence_retains_fallback_defaults():
    result = parse_drawing_filename_evidence("项目总说明.pdf")

    assert result.drawing_no.value == "项目总说明"
    assert result.drawing_no.span is None
    assert result.discipline.value == "general"
    assert result.discipline.confidence == pytest.approx(0.4)
    assert result.title.value == "项目总说明"
    assert result.title.confidence == pytest.approx(0.4)
    assert result.version.value == "A"
    assert result.version.confidence == pytest.approx(0.3)


# ── 真实工程样本（上海大歌剧院竣工图命名规范）────────────────────

@pytest.mark.unit
def test_real_sample_structure_multi_segment_no():
    """结构-竣工图-S-0-11-103C-…：专业全称映射 + 多段图号 + 尾字母版次"""
    result = parse_drawing_filename(
        "结构-竣工图-S-0-11-103C-南区（大、中歌剧厅）深台仓结构图（三）.pdf"
    )
    assert result["discipline"] == "structure"
    assert result["drawing_no"] == "S-0-11-103C"
    assert result["version"] == "C"
    assert "深台仓结构图" in result["title"]


@pytest.mark.unit
def test_real_sample_dotted_segment_no():
    result = parse_drawing_filename(
        "结构-竣工图-S-0-31-102.01C-南区（大、中歌剧厅）一层主梁配筋图（一）.pdf"
    )
    assert result["drawing_no"] == "S-0-31-102.01C"
    assert result["version"] == "C"


@pytest.mark.unit
def test_real_sample_steel_structure_keyword():
    result = parse_drawing_filename("结构-竣工图-S-0-00-011B-钢结构统一说明（一）.pdf")
    assert result["discipline"] == "structure"
    assert result["drawing_no"] == "S-0-00-011B"
    assert result["version"] == "B"


@pytest.mark.unit
def test_real_sample_mep_full_names():
    assert parse_drawing_filename("给排水-竣工图-P-1-01-001-地下泵房大样.pdf")["discipline"] == "mep"
    assert parse_drawing_filename("建筑电气竣工图-E-2-01-001-配电平面.pdf")["discipline"] == "mep"


@pytest.mark.unit
def test_real_sample_architecture_full_name():
    assert parse_drawing_filename("建筑-竣工图-A-1-01-001-一层平面图.pdf")["discipline"] == "architecture"


@pytest.mark.unit
def test_real_sample_no_standard_number_falls_back():
    """围护目录样式：02 环境总平图.pdf —— 无标准图号时安全兜底"""
    result = parse_drawing_filename("02 环境总平图.pdf")
    assert result["discipline"] == "general"
    assert result["drawing_no"]
    assert result["version"] == "A"
