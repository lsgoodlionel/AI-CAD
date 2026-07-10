"""图种判别器测试（B-01：平面/剖面/立面/详图 + 置信度降级）。

覆盖：
- filename_parser 图种关键词匹配（优先级：剖面>立面>平面>详图）
- classify_view_type 关键词判别 + 多源兜底 + 几何佐证 + 低置信度 uncertain
- 验收样本集（≥30 张，四类覆盖）准确率 ≥90%、剖/立面召回 ≥95%
"""
import pytest

from core.model3d.types import DrawingGeometry
from services.drawing_filename_parser import (
    VIEW_TYPE_DETAIL,
    VIEW_TYPE_ELEVATION,
    VIEW_TYPE_PLAN,
    VIEW_TYPE_SECTION,
    match_view_type_keyword,
)
from services.drawing_view_classifier import ViewTypeResult, classify_view_type


# ── filename_parser：图种关键词匹配 ─────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize(
    "text,expected",
    [
        ("A-A剖面图", VIEW_TYPE_SECTION),
        ("1-1剖面", VIEW_TYPE_SECTION),
        ("楼梯剖视图", VIEW_TYPE_SECTION),
        ("墙身剖切详图", VIEW_TYPE_SECTION),
        ("南立面图", VIEW_TYPE_ELEVATION),
        ("①-⑨立面", VIEW_TYPE_ELEVATION),
        ("East Elevation", VIEW_TYPE_ELEVATION),
        ("一层平面图", VIEW_TYPE_PLAN),
        ("标准层平面", VIEW_TYPE_PLAN),
        ("Ground Floor Plan", VIEW_TYPE_PLAN),
        ("地下二层顶板", VIEW_TYPE_PLAN),
        ("卫生间大样图", VIEW_TYPE_DETAIL),
        ("梁柱节点详图", VIEW_TYPE_DETAIL),
        ("楼梯做法", VIEW_TYPE_DETAIL),
    ],
)
def test_match_view_type_keyword_basic(text, expected):
    hit = match_view_type_keyword(text)
    assert hit is not None
    assert hit.view_type == expected


@pytest.mark.unit
def test_match_view_type_priority_section_over_detail():
    """剖面与详图并存 → 剖面胜（保留 z 信息，剖/立面召回优先）"""
    hit = match_view_type_keyword("楼梯剖面大样图")
    assert hit.view_type == VIEW_TYPE_SECTION


@pytest.mark.unit
def test_match_view_type_priority_elevation_over_plan():
    hit = match_view_type_keyword("正立面及一层平面参照")
    assert hit.view_type == VIEW_TYPE_ELEVATION


@pytest.mark.unit
def test_match_view_type_priority_plan_over_detail():
    hit = match_view_type_keyword("标准层平面大样")
    assert hit.view_type == VIEW_TYPE_PLAN


@pytest.mark.unit
def test_match_view_type_no_keyword_returns_none():
    assert match_view_type_keyword("配筋表") is None
    assert match_view_type_keyword("") is None


# ── classify_view_type：关键词主链 ──────────────────────────────

@pytest.mark.unit
def test_classify_from_title_high_confidence():
    result = classify_view_type({"title": "A-A剖面图"})
    assert isinstance(result, ViewTypeResult)
    assert result.view_type == VIEW_TYPE_SECTION
    assert result.confidence >= 0.9
    assert result.uncertain is False
    assert result.evidence["keyword_source"] == "title"
    assert result.evidence["keyword"]


@pytest.mark.unit
def test_classify_falls_back_to_filename_when_title_absent():
    result = classify_view_type(
        {"title": "梁配筋图", "filename": "结施-30 A-A剖面.pdf"}
    )
    assert result.view_type == VIEW_TYPE_SECTION
    assert result.evidence["keyword_source"] == "filename"
    # 非 title 源置信度略低
    assert 0.7 <= result.confidence < 0.9


@pytest.mark.unit
def test_classify_title_wins_over_other_sources():
    result = classify_view_type(
        {"title": "南立面图", "folder_path": "剖面图集"}
    )
    assert result.view_type == VIEW_TYPE_ELEVATION
    assert result.evidence["keyword_source"] == "title"


@pytest.mark.unit
def test_classify_no_keyword_no_geometry_is_unknown_uncertain():
    result = classify_view_type({"title": "钢筋材料表"})
    assert result.view_type == "unknown"
    assert result.uncertain is True
    assert result.evidence["needs_vlm"] is True


# ── 几何佐证 ────────────────────────────────────────────────────

def _plan_geometry() -> DrawingGeometry:
    """双向长轴线网格 → 平面几何签名。"""
    lines = []
    for x in (60, 200, 340, 480):  # 竖向长线
        lines.append((x, 40, x, 760))
    for y in (60, 220, 400, 600):  # 横向长线
        lines.append((40, y, 560, y))
    return DrawingGeometry(page_w=600, page_h=800, lines=lines)


def _section_geometry() -> DrawingGeometry:
    """单向 + 密集水平标高线 + 标高文本 → 剖面/立面几何签名。"""
    lines = [(40, y, 560, y) for y in (120, 280, 440, 600)]  # 水平标高线
    lines.append((80, 40, 80, 760))  # 单条竖向轮廓
    texts = [
        (570, 120, "±0.000"),
        (570, 280, "4.200"),
        (570, 440, "8.400"),
        (570, 600, "12.600"),
    ]
    return DrawingGeometry(page_w=600, page_h=800, lines=lines, texts=texts)


@pytest.mark.unit
def test_geometry_confirms_plan_keyword_boosts_confidence():
    base = classify_view_type({"title": "一层平面图"})
    boosted = classify_view_type({"title": "一层平面图", "geometry": _plan_geometry()})
    assert boosted.view_type == VIEW_TYPE_PLAN
    assert boosted.confidence > base.confidence
    assert boosted.evidence["geometry_signature"] == VIEW_TYPE_PLAN


@pytest.mark.unit
def test_geometry_conflict_flags_uncertain():
    """标题判平面，几何却是剖面签名 → 冲突，降置信 + uncertain + needs_vlm。"""
    result = classify_view_type({"title": "一层平面图", "geometry": _section_geometry()})
    assert result.uncertain is True
    assert result.evidence["conflict"] is True
    assert result.evidence["needs_vlm"] is True


@pytest.mark.unit
def test_detail_keyword_with_plan_geometry_neither_agrees_nor_conflicts():
    """详图关键词 + 平面几何：几何既不佐证也不冲突，保持基础置信、不标冲突。"""
    result = classify_view_type({"title": "卫生间大样图", "geometry": _plan_geometry()})
    assert result.view_type == VIEW_TYPE_DETAIL
    assert result.confidence == pytest.approx(0.9)
    assert result.evidence["conflict"] is False
    assert result.evidence["geometry_signature"] == VIEW_TYPE_PLAN


@pytest.mark.unit
def test_inconclusive_geometry_returns_no_signature():
    """稀疏/畸形几何（含短线段、无标高、非网格）→ 无签名，回落 unknown。"""
    geom = DrawingGeometry(
        page_w=600,
        page_h=800,
        lines=[(0, 0, 1), (10, 10, 12, 12)],  # 畸形（<4 元素）+ 短斜线
    )
    result = classify_view_type({"title": "钢筋表", "geometry": geom})
    assert result.view_type == "unknown"
    assert result.evidence["geometry_signature"] is None
    assert result.uncertain is True


@pytest.mark.unit
def test_geometry_only_plan_without_keyword():
    result = classify_view_type({"title": "详见说明", "geometry": _plan_geometry()})
    assert result.view_type == VIEW_TYPE_PLAN
    assert result.evidence["keyword_source"] is None
    assert result.evidence["geometry_signature"] == VIEW_TYPE_PLAN


@pytest.mark.unit
def test_geometry_section_signature_without_keyword_stays_unknown():
    """几何像剖面/立面但无法区分二者，且无关键词 → unknown + 需 VLM。"""
    result = classify_view_type({"title": "详见说明", "geometry": _section_geometry()})
    assert result.view_type == "unknown"
    assert result.uncertain is True
    assert result.evidence["needs_vlm"] is True


# ── 结构 / 兜底 ─────────────────────────────────────────────────

@pytest.mark.unit
def test_evidence_contains_contract_keys():
    result = classify_view_type({"title": "A-A剖面图"})
    assert {
        "keyword_source",
        "keyword",
        "geometry_signature",
        "needs_vlm",
        "conflict",
    } <= set(result.evidence)


@pytest.mark.unit
def test_empty_drawing_is_unknown():
    result = classify_view_type({})
    assert result.view_type == "unknown"
    assert result.uncertain is True


# ── 验收：样本集准确率 ─────────────────────────────────────────

# (drawing, 期望 view_type)；覆盖四类，模拟真实图名用词多样性
_SAMPLE_SET: tuple[tuple[dict, str], ...] = (
    ({"title": "一层平面图"}, VIEW_TYPE_PLAN),
    ({"title": "二层平面图"}, VIEW_TYPE_PLAN),
    ({"title": "标准层平面布置图"}, VIEW_TYPE_PLAN),
    ({"title": "屋顶层平面图"}, VIEW_TYPE_PLAN),
    ({"title": "地下一层平面图"}, VIEW_TYPE_PLAN),
    ({"title": "总平面图"}, VIEW_TYPE_PLAN),
    ({"title": "基础平面布置图"}, VIEW_TYPE_PLAN),
    ({"title": "一层梁配筋平面图"}, VIEW_TYPE_PLAN),
    ({"filename": "建施-05 三层平面图.pdf"}, VIEW_TYPE_PLAN),
    ({"title": "顶板配筋平面"}, VIEW_TYPE_PLAN),
    ({"title": "1-1剖面图"}, VIEW_TYPE_SECTION),
    ({"title": "A-A剖面图"}, VIEW_TYPE_SECTION),
    ({"title": "楼梯剖面图"}, VIEW_TYPE_SECTION),
    ({"title": "墙身剖面大样"}, VIEW_TYPE_SECTION),
    ({"title": "2-2剖视图"}, VIEW_TYPE_SECTION),
    ({"filename": "结施-31 B-B剖面.pdf"}, VIEW_TYPE_SECTION),
    ({"title": "电梯井道剖面图"}, VIEW_TYPE_SECTION),
    ({"title": "承台剖面详图"}, VIEW_TYPE_SECTION),
    ({"title": "南立面图"}, VIEW_TYPE_ELEVATION),
    ({"title": "北立面图"}, VIEW_TYPE_ELEVATION),
    ({"title": "东立面图"}, VIEW_TYPE_ELEVATION),
    ({"title": "①-⑨轴立面图"}, VIEW_TYPE_ELEVATION),
    ({"title": "正立面图"}, VIEW_TYPE_ELEVATION),
    ({"filename": "建施-20 背立面.pdf"}, VIEW_TYPE_ELEVATION),
    ({"title": "楼梯间侧立面"}, VIEW_TYPE_ELEVATION),
    ({"title": "卫生间大样图"}, VIEW_TYPE_DETAIL),
    ({"title": "梁柱节点详图"}, VIEW_TYPE_DETAIL),
    ({"title": "女儿墙做法详图"}, VIEW_TYPE_DETAIL),
    ({"title": "门窗大样"}, VIEW_TYPE_DETAIL),
    ({"title": "集水坑大样图"}, VIEW_TYPE_DETAIL),
    ({"title": "散水节点"}, VIEW_TYPE_DETAIL),
)


@pytest.mark.unit
def test_sample_set_overall_accuracy_at_least_90pct():
    correct = sum(
        classify_view_type(drawing).view_type == expected
        for drawing, expected in _SAMPLE_SET
    )
    assert correct / len(_SAMPLE_SET) >= 0.90


@pytest.mark.unit
def test_sample_set_section_and_elevation_recall_at_least_95pct():
    targets = [
        (d, e) for d, e in _SAMPLE_SET
        if e in (VIEW_TYPE_SECTION, VIEW_TYPE_ELEVATION)
    ]
    recalled = sum(classify_view_type(d).view_type == e for d, e in targets)
    assert recalled / len(targets) >= 0.95
