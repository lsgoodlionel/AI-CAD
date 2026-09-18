"""反算参数：把场景里的米坐标还原成页面点。

没有它，模型就叠不回图纸核对 —— 存量 v85 实测反算到 x=-170（纸外）。
这组用例锁住三件事：换算与 `meters_to_page` 一致、位移要扣掉、
逆推不了时**抛异常而不是给个差不多的坐标**。
"""
from __future__ import annotations

import pytest

from core.model3d.element_frame import ElementFrame, FrameNotReversible, frame_of
from core.model3d.yolo_export import meters_to_page

FRAME = {"scale_m_pt": 0.05, "origin_pt": [120.0, 80.0], "page_h": 2384.0}


@pytest.mark.unit
def test_round_trip_matches_the_shared_conversion():
    frame = ElementFrame.from_dict(FRAME)
    assert frame.to_page(3.0, 4.0) == meters_to_page(
        3.0, 4.0, 0.05, (120.0, 80.0), 2384.0)


@pytest.mark.unit
def test_the_shift_applied_at_merge_time_is_taken_back_out():
    """构件并入楼层前被平移过；不扣掉就会整体错位一个配准偏移量。"""
    moved = ElementFrame.from_dict({**FRAME, "shift_m": [12.0, -5.0]})
    plain = ElementFrame.from_dict(FRAME)
    assert moved.to_page(15.0, -1.0) == plain.to_page(3.0, 4.0)


@pytest.mark.unit
def test_world_placed_drawings_refuse_to_be_reversed():
    """世界坐标摆放是旋转+平移。给个「差不多」的坐标比没有更坏 ——
    叠错位的框看起来和叠对了一样，只是指向别的东西。"""
    placed = ElementFrame.from_dict({**FRAME, "placed": True})
    with pytest.raises(FrameNotReversible, match="旋转"):
        placed.to_page(1.0, 2.0)


@pytest.mark.unit
def test_incomplete_frame_refuses_instead_of_guessing():
    for bad in ({}, {"scale_m_pt": 0.0, "page_h": 100.0}, {"scale_m_pt": 0.05}):
        with pytest.raises(FrameNotReversible):
            ElementFrame.from_dict(bad).to_page(1.0, 1.0)


@pytest.mark.unit
def test_frame_of_returns_none_when_the_scene_predates_this_field():
    """存量场景没有这个字段 —— 返回 None，让调用方如实说「反算不了」。"""
    assert frame_of({}, "d1") is None
    assert frame_of({"element_frames": {}}, "d1") is None
    assert frame_of({"element_frames": {"d1": FRAME}}, "d1").usable
