"""
core/model3d/circle_detector.py 单测(Phase E3 / 路径B:围护桩圆检测)

栅格 HoughCircles 检测圆 → 米坐标八边形柱。纯变换/几何逻辑离线可测;
HoughCircles 用合成图(numpy 画 3 个圆)验证。
"""
import numpy as np
import pytest

from core.model3d.circle_detector import (
    circle_px_to_meter,
    detect_circles_px,
    octagon_outline,
)


def test_octagon_outline_size_and_center():
    oct8 = octagon_outline(10.0, 20.0, 2.0)
    assert len(oct8) == 8
    # 八边形顶点距圆心均为半径 r
    for x, y in oct8:
        r = ((x - 10.0) ** 2 + (y - 20.0) ** 2) ** 0.5
        assert abs(r - 2.0) < 2e-3  # outline round(,3) 引入 ~mm 级误差,建模足够


def test_circle_px_to_meter_transform():
    # dpi=72 → 1px=1pt;origin=(0,0);scale=0.01 m/pt;page_h=1000pt
    cx_m, cy_m, r_m = circle_px_to_meter(
        100.0, 200.0, 10.0,
        dpi=72, page_h_pt=1000.0, scale_m_pt=0.01, origin_pt=(0.0, 0.0),
    )
    # x: (100-0)*0.01=1.0 ; y 翻转: (1000-200-0)*0.01=8.0 ; r: 10*0.01=0.1
    assert cx_m == pytest.approx(1.0)
    assert cy_m == pytest.approx(8.0)
    assert r_m == pytest.approx(0.1)


def test_detect_circles_px_finds_synthetic_circles():
    # 512x512 白底,画 3 个半径 20 的黑圆
    import cv2
    img = np.full((512, 512), 255, dtype=np.uint8)
    centers = [(100, 100), (250, 300), (400, 150)]
    for cx, cy in centers:
        cv2.circle(img, (cx, cy), 20, 0, 2)
    found = detect_circles_px(img, min_r_px=12, max_r_px=30, param2=20)
    # 至少检出 3 个圆(允许少量重复/邻近)
    assert len(found) >= 3
    # 每个真实圆心附近应有检出
    for cx, cy in centers:
        assert any(abs(f[0] - cx) < 12 and abs(f[1] - cy) < 12 for f in found)


def test_detect_circles_px_empty_on_blank():
    blank = np.full((256, 256), 255, dtype=np.uint8)
    assert detect_circles_px(blank, 10, 30, param2=30) == []
