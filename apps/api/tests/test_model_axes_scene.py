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
