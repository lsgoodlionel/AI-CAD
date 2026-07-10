"""跨视图 z 恢复统一入口测试（B-10）。

recover_z_from_geometries 串联 B-01 判图种 → B-08 轴网 → B-02 剖面标高 →
B-06 立面洞口 → B-09 配准 → B-07 截面表，产出一站式 ZRecoveryResult。
纯平面批次向后兼容（不崩、无强证据）。
"""
import pytest

from core.ai_review.cross_view_z import (
    ZRecoveryResult,
    recover_z,
    recover_z_from_geometries,
)
from core.model3d.types import DrawingGeometry


def _plan_item():
    lines = []
    for x in (100, 300, 500):
        lines.append((x, 40, x, 760))
    for y in (100, 400, 700):
        lines.append((40, y, 560, y))
    texts = [(100, 20, "1"), (300, 20, "2"), (500, 20, "3"),
             (20, 100, "A"), (20, 400, "B"), (20, 700, "C")]
    geom = DrawingGeometry(page_w=600, page_h=800, lines=lines, texts=texts)
    return {"id": "plan1", "title": "一层平面图", "drawing_no": "A-101"}, geom


def _section_item():
    lines = [(40, 760, 560, 760), (40, 560, 560, 560), (40, 360, 560, 360)]
    texts = [
        (575, 760, "±0.000"), (575, 560, "+3.000"), (575, 360, "+6.000"),
        (300, 450, "KL1 300×600"),  # 梁截面标注
    ]
    geom = DrawingGeometry(page_w=600, page_h=800, lines=lines, texts=texts)
    return {"id": "sec1", "title": "1-1剖面图", "drawing_no": "A-501"}, geom


def _elevation_item():
    lines = [(40, 760, 560, 760), (40, 560, 560, 560), (40, 360, 560, 360)]
    for x in (100, 300, 500):
        lines.append((x, 40, x, 780))
    texts = [(575, 760, "±0.000"), (575, 560, "+3.000"), (575, 360, "+6.000"),
             (100, 20, "1"), (300, 20, "2"), (500, 20, "3")]
    rects = [(150, 500, 100, 100, False)]  # 窗洞，顶标高约 3.9m 落在 [0,6]
    geom = DrawingGeometry(page_w=600, page_h=800, lines=lines, texts=texts, rects=rects)
    return {"id": "elev1", "title": "南立面图", "drawing_no": "A-601"}, geom


# ── 三视图齐备：一站式产出 ─────────────────────────────────────

@pytest.mark.unit
def test_full_batch_produces_levels_sections_and_match():
    result = recover_z_from_geometries([_plan_item(), _section_item(), _elevation_item()])

    assert isinstance(result, ZRecoveryResult)
    assert result.view_classification == {
        "plan1": "plan",
        "sec1": "section",
        "elev1": "elevation",
    }
    # 标高表来自剖面
    assert [lvl["elevation_m"] for lvl in result.levels] == [0.0, 3.0, 6.0]
    # 截面表从剖面梁标注实测
    beam = result.component_sections["beam"]
    assert beam.h_m == pytest.approx(0.6)
    assert beam.estimated is False
    # 三视图一致 → 强匹配
    assert result.matched is True
    assert result.registration.consistency_score >= 0.9


# ── 纯平面批次：向后兼容 ───────────────────────────────────────

@pytest.mark.unit
def test_plan_only_batch_no_strong_match_no_crash():
    result = recover_z_from_geometries([_plan_item()])
    assert result.view_classification == {"plan1": "plan"}
    assert result.matched is False
    assert result.levels == ()
    # 无剖面/详图 → 截面全默认估算
    assert result.component_sections["beam"].estimated is True


@pytest.mark.unit
def test_section_only_batch_yields_levels_but_not_matched():
    result = recover_z_from_geometries([_section_item()])
    assert [lvl["elevation_m"] for lvl in result.levels] == [0.0, 3.0, 6.0]
    assert result.matched is False  # 缺立面互校


@pytest.mark.unit
def test_empty_batch_returns_empty_result():
    result = recover_z_from_geometries([])
    assert result.view_classification == {}
    assert result.levels == ()
    assert result.matched is False


# ── 异步壳：优雅跳过无效图纸 ───────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_recover_z_skips_drawings_without_valid_file():
    drawings = [
        {"id": "d1", "title": "一层平面图", "file_key": ""},          # 无文件
        {"id": "d2", "title": "1-1剖面图", "file_key": "x.txt"},      # 非支持格式
    ]

    def _file_getter(_key: str) -> bytes:
        raise AssertionError("不应对无效图纸取字节")

    result = await recover_z(None, "p1", _file_getter, drawings=drawings)
    assert isinstance(result, ZRecoveryResult)
    assert result.matched is False
    assert result.levels == ()
