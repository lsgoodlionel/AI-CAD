"""文件名的「专业-阶段-」前缀必须剥离 —— 第二工程交叉验证暴露的真缺陷。

**发现经过**:导入轨道交通工程(1798 张)时干跑,
`基坑支护-竣工图-01-基坑围护设计施工总说明.pdf` 被解析成
图号=`竣工图-01`(阶段词+序号被当图号)、标题=`基坑支护--基坑围护设计施工总说明`。

回头查大歌剧院才发现**同样的缺陷一直在,只是被掩盖**:
- **2166/2309(93.8%)的 title 混着「专业-竣工图-」前缀**;
- 144 张图号含中文(同类误判)。
它没暴露,是因为大歌剧院图号多以拉丁字母开头(`ZNH-20-102`),
多段正则先命中;而轨道交通的基坑支护图是**纯序号**,一下就错了。

**通用修法**:中文工程图纸文件名的常见结构是
`{专业}-{阶段}-{图号}-{图名}`。专业词与阶段词(竣工图/施工图/招标图…)
都是**行业通用术语**(兜底标准是国标),从左往右逐段剥离,
遇到第一个非此类段即停 —— 所以图名里的「设计说明」不会被误剥。
"""
from __future__ import annotations

import pytest

from services.drawing_filename_parser import parse_drawing_filename


# ── 轨道交通(第二工程):纯序号图号 ────────────────────────────

@pytest.mark.unit
def test_metro_plain_sequence_number():
    """**核心用例**:阶段词不得被当成图号。"""
    got = parse_drawing_filename("基坑支护-竣工图-01-基坑围护设计施工总说明.pdf")
    assert got["drawing_no"] == "01"
    assert got["title"] == "基坑围护设计施工总说明"
    assert got["discipline"] == "structure"


@pytest.mark.unit
def test_metro_lettered_number_keeps_working():
    got = parse_drawing_filename("结构-竣工图-S-31-07A-首层框架梁平面整体配筋图.pdf")
    assert got["drawing_no"] == "S-31-07A"
    assert got["title"] == "首层框架梁平面整体配筋图"


@pytest.mark.unit
def test_metro_decoration_with_spaced_revision():
    """装饰图的版次单独成段且带空格 —— 标题不得把它吃进去。"""
    got = parse_drawing_filename("装饰-竣工图-I-10-02-A- 2F平面布置图.pdf")
    assert got["drawing_no"].startswith("I-10-02")
    assert got["title"] == "2F平面布置图"


@pytest.mark.unit
def test_metro_curtain_wall_letter_digit_code():
    got = parse_drawing_filename("幕墙-竣工图-C1-H58-C1玻璃幕墙横剖节点详图.pdf")
    assert got["title"] == "C1玻璃幕墙横剖节点详图"


# ── 大歌剧院(第一工程):同一缺陷,不得退化 ──────────────────────

@pytest.mark.unit
def test_opera_title_no_longer_carries_the_prefix():
    """**回归**:此前 title=`电气-竣工图-_四层公共广播…`(93.8% 的图如此)。"""
    got = parse_drawing_filename(
        "电气-竣工图-ZNH-20-102-四层公共广播及紧急广播平面图（二）.pdf")
    assert got["drawing_no"] == "ZNH-20-102"
    assert got["title"] == "四层公共广播及紧急广播平面图（二）"


@pytest.mark.unit
def test_opera_title_with_stray_separators():
    """实测文件名有 `--` 与 `_` 残留,剥离后要清干净。"""
    got = parse_drawing_filename(
        "结构-竣工图-ST-41-032C--楼梯ST-28、29、30结构详图.pdf")
    assert got["drawing_no"] == "ST-41-032C"
    assert got["title"] == "楼梯ST-28、29、30结构详图"


# ── 剥离必须保守 ─────────────────────────────────────────────

@pytest.mark.unit
def test_stage_word_inside_the_title_is_kept():
    """**只剥前缀段** —— 图名里的「设计说明」「竣工验收」不能被吃掉。"""
    got = parse_drawing_filename("建筑-竣工图-A-00-20A-设计说明20.pdf")
    assert got["title"] == "设计说明20"
    got2 = parse_drawing_filename("建筑-竣工图-A-00-21-竣工验收说明.pdf")
    assert got2["title"] == "竣工验收说明"


@pytest.mark.unit
def test_filename_without_any_prefix_is_untouched():
    """没有专业/阶段前缀的文件名保持原行为。"""
    got = parse_drawing_filename("S-31-07A-首层框架梁平面整体配筋图.pdf")
    assert got["drawing_no"] == "S-31-07A"
    assert got["title"] == "首层框架梁平面整体配筋图"


@pytest.mark.unit
def test_discipline_still_read_from_the_stripped_prefix():
    """专业**判定**仍要用到被剥的那段 —— 剥的是标题,不是证据。"""
    assert parse_drawing_filename(
        "暖通-竣工图-M-03-05A-空调通风风管设备表（五）.pdf")["discipline"] == "mep"
    assert parse_drawing_filename(
        "装饰-竣工图-I-30-56-A-地面通用节点大样图.pdf")["discipline"] == "decoration"


@pytest.mark.unit
def test_stage_only_name_does_not_explode():
    """整个文件名只有专业+阶段时不能剥成空标题。"""
    got = parse_drawing_filename("结构-竣工图.pdf")
    assert got["title"], "标题不得为空"


@pytest.mark.unit
def test_sequence_number_with_attached_revision():
    """**实测 80 张**:`09A` 是序号+版次且无分隔符,纯序号模式原先咬不住。

    后随断言(分隔符或结尾)保证图名里的 `2F平面布置图` 不会被误认 ——
    `2F` 后面紧跟中文,不构成图号。
    """
    got = parse_drawing_filename("基坑支护-竣工图-09A-地下连续墙预埋件详图.pdf")
    assert got["drawing_no"] == "09A"
    assert got["version"] == "A"
    assert got["title"] == "地下连续墙预埋件详图"


@pytest.mark.unit
def test_floor_token_at_title_head_is_not_a_number():
    """剥离后若图名直接以楼层开头,不得把 `2F` 当图号。"""
    got = parse_drawing_filename("装饰-竣工图-2F平面布置图.pdf")
    assert got["drawing_no"] != "2F"


@pytest.mark.unit
def test_dotted_code_number():
    """**实测 30 张**:`SPEC.001` 是「字母代码.序号」形态(通用,非工程特有)。"""
    got = parse_drawing_filename("建筑-竣工图-SPEC.001-设计说明.pdf")
    assert got["drawing_no"] == "SPEC.001"
    assert got["title"] == "设计说明"


@pytest.mark.unit
def test_a_drawing_index_has_no_number():
    """**图纸目录/封面本就没有图号** —— 兜底给主干是正确行为,不是错误。

    残留统计里 42 张「目录」曾被我算进误判,实测它们本无图号:
    真实错误率 1.8% 而非 4.2%。**统计口径错会把正确行为当缺陷修**。
    """
    got = parse_drawing_filename("结构-竣工图-目录.pdf")
    assert got["title"] == "目录"
