"""立面洞口识别测试（B-06）。

合成立面几何：竖向轴线（带轴号）+ 标高线/文本（供 z 标定）+ 门窗洞口矩形。
"""
import pytest

from core.model3d.elevation_opening_extractor import (
    ElevationOpenings,
    Opening,
    extract_elevation_openings,
)
from core.model3d.types import DrawingGeometry


def _elevation_geom(
    *,
    rects: list[tuple[float, float, float, float, bool]],
    rect_layers: list[str] | None = None,
    rect_blocks: list[str] | None = None,
    with_levels: bool = True,
    with_axes: bool = True,
) -> DrawingGeometry:
    lines: list[tuple[float, float, float, float]] = []
    texts: list[tuple[float, float, str]] = []
    # z 标定：三条水平标高线 + 标高文本（y 越小标高越高 → 斜率负）
    if with_levels:
        for y, elev in ((760, 0.0), (560, 3.0), (360, 6.0)):
            lines.append((40.0, y, 560.0, y))
            texts.append((575.0, y, f"{elev:+.3f}".replace("+0.000", "±0.000")))
    # 竖向轴线 + 轴号（横向 1/2/3）
    if with_axes:
        for x, label in ((100, "1"), (300, "2"), (500, "3")):
            lines.append((x, 40.0, x, 780.0))
            texts.append((x, 20.0, label))
    return DrawingGeometry(
        page_w=600,
        page_h=800,
        lines=lines,
        rects=rects,
        texts=texts,
        rect_layers=rect_layers or [""] * len(rects),
        rect_blocks=rect_blocks or [""] * len(rects),
    )


# ── 基本识别 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_extracts_window_opening_with_dimensions():
    # 窗洞：x=150,y=500,w=100,h=100（filled=False）
    geom = _elevation_geom(rects=[(150, 500, 100, 100, False)])
    result = extract_elevation_openings(geom)

    assert isinstance(result, ElevationOpenings)
    assert len(result.openings) == 1
    opening = result.openings[0]
    assert isinstance(opening, Opening)
    assert opening.width_m == pytest.approx(1.5, abs=0.05)
    assert opening.height_m == pytest.approx(1.5, abs=0.05)
    # 标高：y=500→3.9m, y=600→2.4m → sill≈2.4 head≈3.9
    assert opening.sill_h_m == pytest.approx(2.4, abs=0.1)
    assert opening.head_h_m == pytest.approx(3.9, abs=0.1)
    assert opening.evidence["dimension_missing"] is False


@pytest.mark.unit
def test_door_layer_classified_as_door():
    geom = _elevation_geom(
        rects=[(150, 600, 90, 140, False)],
        rect_blocks=["M-1021"],  # 门块 → door
    )
    result = extract_elevation_openings(geom)
    assert result.openings[0].kind == "door"


@pytest.mark.unit
def test_window_block_classified_as_window():
    geom = _elevation_geom(
        rects=[(150, 500, 100, 100, False)],
        rect_blocks=["C-1518"],  # 窗块 → window
    )
    assert result_kind(geom) == "window"


def result_kind(geom):
    return extract_elevation_openings(geom).openings[0].kind


@pytest.mark.unit
def test_geometry_door_heuristic_tall_narrow():
    """无图层时几何启发：高瘦（h≥1.9m, w≤1.6m）→ 门。"""
    # x=150,y=460,w=60,h=140 → w_m=0.9, h_m=2.1
    geom = _elevation_geom(rects=[(150, 460, 60, 140, False)])
    assert result_kind(geom) == "door"


@pytest.mark.unit
def test_axis_ref_populated_from_grid():
    geom = _elevation_geom(rects=[(150, 500, 100, 100, False)])
    opening = extract_elevation_openings(geom).openings[0]
    assert opening.axis_ref  # 洞口落在轴 1~2 之间


# ── 降级 / 边界 ─────────────────────────────────────────────────

@pytest.mark.unit
def test_no_levels_marks_dimension_missing_but_returns_geometry():
    """无标高标定 → 洞口按图层识别，返回几何但标 dimension_missing、尺寸为 None。"""
    geom = _elevation_geom(
        rects=[(150, 500, 100, 100, False)],
        rect_blocks=["C-1518"],
        with_levels=False,
    )
    result = extract_elevation_openings(geom)
    assert result.dimension_missing is True
    opening = result.openings[0]
    assert opening.sill_h_m is None
    assert opening.head_h_m is None
    assert opening.evidence["dimension_missing"] is True


@pytest.mark.unit
def test_full_building_outline_not_an_opening():
    """整栋轮廓大矩形（超洞口尺寸上限）不计为洞口。"""
    geom = _elevation_geom(rects=[(60, 60, 480, 680, False)])
    result = extract_elevation_openings(geom)
    assert result.openings == ()


@pytest.mark.unit
def test_duplicate_overlapping_rects_deduped():
    """同一洞口的双线框（近乎重合）去重为一。"""
    geom = _elevation_geom(
        rects=[(150, 500, 100, 100, False), (151, 501, 99, 99, False)]
    )
    assert len(extract_elevation_openings(geom).openings) == 1


@pytest.mark.unit
def test_row_of_windows_not_deduped():
    """成排窗（不同 x）为多个独立洞口，不去重。"""
    geom = _elevation_geom(
        rects=[
            (120, 500, 60, 100, False),
            (240, 500, 60, 100, False),
            (360, 500, 60, 100, False),
        ]
    )
    assert len(extract_elevation_openings(geom).openings) == 3


@pytest.mark.unit
def test_no_rects_returns_reason():
    geom = _elevation_geom(rects=[])
    result = extract_elevation_openings(geom)
    assert result.openings == ()
    assert result.reason == "no_rects"
