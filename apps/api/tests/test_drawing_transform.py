"""
services/drawing_transform.py 单测(Phase E 路径C-A1)

pt→米变换的正解/persist。纯函数 pt_to_meter 与 _Ctx.to_m 同口径。
"""
import pytest

from services.drawing_transform import DrawingTransform, pt_to_meter


def test_pt_to_meter_matches_ctx_formula():
    # origin=(100,50) pt, scale=0.01 m/pt, page_h=1000 pt
    t = DrawingTransform(scale_m_pt=0.01, origin_x=100.0, origin_y=50.0, page_h=1000.0)
    # 点 (200, 300) pt
    x_m, y_m = pt_to_meter(200.0, 300.0, t)
    # x: (200-100)*0.01 = 1.0
    # y: ((1000-300)-50)*0.01 = (700-50)*0.01 = 6.5
    assert x_m == pytest.approx(1.0)
    assert y_m == pytest.approx(6.5)


def test_pt_to_meter_origin_maps_to_near_zero():
    t = DrawingTransform(scale_m_pt=0.02, origin_x=100.0, origin_y=50.0, page_h=800.0)
    # 原点在页面坐标 (origin_x, page_h - origin_y) = (100, 750)
    x_m, y_m = pt_to_meter(100.0, 750.0, t)
    assert x_m == pytest.approx(0.0)
    assert y_m == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_persist_transform_upserts(fake_db):
    from services.drawing_transform import persist_transform

    t = DrawingTransform(scale_m_pt=0.01, origin_x=1.0, origin_y=2.0,
                         page_h=1000.0, confidence=0.8)
    await persist_transform(fake_db, project_id="p1", drawing_id="d1", transform=t)

    sql = fake_db.execute.call_args.args[0]
    params = fake_db.execute.call_args.args[1]
    assert "INSERT INTO drawing_transform" in sql
    assert "ON CONFLICT" in sql
    assert params["scale_m_pt"] == 0.01
    assert params["drawing_id"] == "d1"


def test_transform_from_geometry_none_on_bad_scale():
    """比例尺检测失败(<=0)→ 返回 None(不落无效变换)。"""
    from services.drawing_transform import transform_from_geometry

    class _Geom:
        lines = []
        texts = []
        page_w = 0.0
        page_h = 0.0
    assert transform_from_geometry(_Geom()) is None
