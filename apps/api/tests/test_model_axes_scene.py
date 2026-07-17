"""
E2 轴网入 scene 单测(Phase E2-1)

- build_floor_elements:配准参考轴网(ref_axes)不再算完即弃,
  以 scene 格式带出 meta["axes"](含轴号/坐标/来源图纸)
- build_scene:floor dict 携带 axes 字段(前端轴网层数据源)
"""
from unittest.mock import AsyncMock

import pytest

from services import model_elements


def _recognize_result(axes: dict) -> dict:
    return {
        "elements": {k: [] for k in model_elements.EMPTY_ELEMENTS},
        "axes": axes,
    }


@pytest.mark.asyncio
async def test_build_floor_elements_carries_axes_in_meta(monkeypatch):
    """首张带轴号图的轴网以 scene 格式出现在 meta.axes(含来源图纸)。"""
    labeled = {
        "x": [("1", 0.0), ("2", 8.4)],
        "y": [("A", 0.0), ("B", 12.5)],
        "elevations": [],
    }
    recognize = AsyncMock(return_value=_recognize_result(labeled))
    monkeypatch.setattr(model_elements, "_recognize_one", recognize)

    drawings = [{
        "id": "d-plan-1", "title": "一层墙柱结构平面图",
        "discipline": "structure", "file_key": "k.pdf",
    }]
    _elements, _yolo, meta = await model_elements.build_floor_elements(
        None, drawings, lambda key: b""
    )

    axes = meta.get("axes")
    assert axes is not None
    assert axes["x"] == [
        {"label": "1", "coord": 0.0},
        {"label": "2", "coord": 8.4},
    ]
    assert axes["y"][1] == {"label": "B", "coord": 12.5}
    assert axes["source_drawing_id"] == "d-plan-1"


@pytest.mark.asyncio
async def test_axes_aggregate_across_multiple_drawings(monkeypatch):
    """跨该层多张图聚合轴网:第二张图补充第一张没有的轴线(同坐标系)。"""
    calls = iter([
        _recognize_result({"x": [("1", 0.0), ("2", 8.4)], "y": [("A", 0.0)],
                           "elevations": []}),
        _recognize_result({"x": [("1", 0.0), ("3", 16.8)], "y": [("B", 12.5)],
                           "elevations": []}),
    ])

    async def _fake_recognize(*a, **k):
        return next(calls, None)
    monkeypatch.setattr(model_elements, "_recognize_one", _fake_recognize)

    drawings = [
        {"id": "d1", "title": "一层结构平面图", "discipline": "structure", "file_key": "a.pdf"},
        {"id": "d2", "title": "一层梁配筋图", "discipline": "structure", "file_key": "b.pdf"},
    ]
    _e, _y, meta = await model_elements.build_floor_elements(None, drawings, lambda k: b"")

    axes = meta["axes"]
    x_labels = {e["label"] for e in axes["x"]}
    y_labels = {e["label"] for e in axes["y"]}
    # 两张图的轴线并集(1/2 来自第一张,3 来自第二张;A/B 各一张)
    assert {"1", "2", "3"} <= x_labels
    assert {"A", "B"} <= y_labels


def test_axes_payload_denoises_junk_labels():
    from services.model_elements import _axes_scene_payload
    axes = {"x": [("1", 0.0), ("说明文字很长的噪声", 5.0), ("2", 8.4)], "y": []}
    payload = _axes_scene_payload(axes, "d1")
    labels = {e["label"] for e in payload["x"]}
    assert "1" in labels and "2" in labels
    assert "说明文字很长的噪声" not in labels  # 超长非轴号被去噪


@pytest.mark.asyncio
async def test_build_floor_elements_axes_absent_without_labels(monkeypatch):
    """无带轴号图时 meta 不携带 axes(前端判空不渲染)。"""
    unlabeled = {"x": [], "y": [], "elevations": []}
    recognize = AsyncMock(return_value=_recognize_result(unlabeled))
    monkeypatch.setattr(model_elements, "_recognize_one", recognize)

    drawings = [{
        "id": "d-1", "title": "一层墙柱结构平面图",
        "discipline": "structure", "file_key": "k.pdf",
    }]
    _elements, _yolo, meta = await model_elements.build_floor_elements(
        None, drawings, lambda key: b""
    )

    assert meta.get("axes") is None
