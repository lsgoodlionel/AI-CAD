"""YOLO 后端：自训权重接入 Phase C 的 SpottingBackend 契约。

**推理必须与训练对称地切片**：模型是在 640px 块上训的，
块内框中位 15.5×15.6 px；直接喂整图（框只有 3.7×4.8 px）等于
喂它没见过的尺度，检不出来是必然的。

跨块重复必须去重：切片有 64px 重叠，同一根柱会在相邻两块各检出一次。
"""
import pytest


@pytest.mark.unit
def test_tile_detection_maps_back_to_page_coordinates():
    """块内坐标要换算回整图——**否则每个框都落在左上角 640px 里**。"""
    from core.model3d.spotting.yolo_backend import tile_box_to_page

    box = tile_box_to_page((10.0, 20.0, 50.0, 60.0), tile=(576, 1152, 1216, 1792))
    assert box == (586.0, 1172.0, 626.0, 1212.0)


@pytest.mark.unit
def test_overlapping_duplicates_are_merged():
    """切片有 64px 重叠，同一根柱会在相邻两块各检出一次。"""
    from core.model3d.spotting.yolo_backend import dedupe_boxes

    a = {"bbox": (100.0, 100.0, 130.0, 130.0), "confidence": 0.8, "category": "column"}
    b = {"bbox": (102.0, 101.0, 132.0, 131.0), "confidence": 0.9, "category": "column"}
    out = dedupe_boxes([a, b], iou_threshold=0.5)
    assert len(out) == 1
    assert out[0]["confidence"] == 0.9, "重复时保留置信度更高的那个"


@pytest.mark.unit
def test_different_categories_are_not_merged():
    """**不同类别不能合并**——柱与板重叠是常态（柱站在板上）。"""
    from core.model3d.spotting.yolo_backend import dedupe_boxes

    out = dedupe_boxes([
        {"bbox": (100.0, 100.0, 130.0, 130.0), "confidence": 0.8, "category": "column"},
        {"bbox": (100.0, 100.0, 130.0, 130.0), "confidence": 0.7, "category": "slab"},
    ], iou_threshold=0.5)
    assert len(out) == 2


@pytest.mark.unit
def test_missing_weights_degrade_visibly():
    """**没有权重时要明确说出来**，不能静默返回空结果——
    那会让「模型没接上」看起来像「这张图没有构件」。"""
    from core.model3d.spotting.yolo_backend import YoloSpottingBackend

    backend = YoloSpottingBackend(weights_path="/nonexistent/best.pt")
    result = backend.spot(None)
    assert result.candidates == ()
    assert any("权重" in w or "weights" in w.lower() for w in result.warnings)


@pytest.mark.unit
def test_backend_satisfies_the_phase_c_contract():
    """接的是 Phase C 已有的契约，不另起一套。"""
    from core.model3d.spotting.types import SpottingBackend
    from core.model3d.spotting.yolo_backend import YoloSpottingBackend

    assert isinstance(YoloSpottingBackend(weights_path="/x"), SpottingBackend)
