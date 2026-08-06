"""设备符号场闸:只标记,不拦截。

**为什么改**:上一版在带数 > 40 时**直接不产出轴线**。实测
`A-10-04C 一层完整平面图` 有 **42 条带**——只超 2 条就被误杀,
整层轴线全丢。而它是最核心的一张平面图。

三条替代判据全部被实测证伪:

| 判据 | 一层平面 | 地下二层 | 轴网定位 | 喷淋 P-21-09C | 结论 |
|---|---|---|---|---|---|
| 带数 | 42 | 13 | 9 | 200 | 阈值贴着真轴网,误伤 |
| 最长带 | 22 | 24 | 24 | **58** | 喷淋反而更长 |
| 圈内有笔画(§8.0.2) | 100% | 100% | 100% | **100%** | 分不开 |

既然分不开,就**不能让判错的代价是丢掉整层轴线**。改为照常产出 +
打 `suspect_symbol_field` 标记,由消费方(入 3D 场景、写世界锚点)跳过。
失败模式从「漏掉真轴网」变成「标记不准」。
"""
from __future__ import annotations

import pytest

from services.axis_recognition import (
    SYMBOL_FIELD_BAND_HINT, axes_to_scene, is_suspect_symbol_field, recognize,
)
from tests.test_axis_recognition import CIRCLES, PAGE_H, PAGE_W, _transform


@pytest.mark.unit
def test_measured_real_grids_are_not_suspect():
    """**实测**四张真轴网的带数都不该被判为符号场。

    A-10-04C 一层完整平面图 42 条带是关键用例 —— 旧阈值 40 正好把它误杀。
    """
    for band_count, name in ((42, "A-10-04C 一层完整平面图"),
                             (17, "A-10-03C 地下一层"),
                             (13, "A-10-02C 地下二层"),
                             (9, "A-01-02A 轴网定位图")):
        assert not is_suspect_symbol_field(band_count), name


@pytest.mark.unit
def test_measured_symbol_field_is_suspect():
    """实测喷淋图 P-21-09C:2340 个圈成 200 条带。"""
    assert is_suspect_symbol_field(200)


@pytest.mark.unit
def test_normal_drawing_is_not_suspect():
    got = recognize(CIRCLES, strokes=[], segments=[], page_w=PAGE_W,
                    page_h=PAGE_H, read_text=lambda leader: [])
    assert got["suspect_symbol_field"] is False
    assert got["axis_count"] > 0


@pytest.mark.unit
def test_suspect_axes_do_not_reach_the_3d_scene():
    """标记的用处在这里:可疑轴网不进模型,但识别结果仍留档可查。"""
    got = recognize(CIRCLES, strokes=[], segments=[], page_w=PAGE_W,
                    page_h=PAGE_H, read_text=lambda leader: [])
    assert got["axes"], "夹具本身要能出轴线,否则这条测的是空集"
    # 同一批轴线:未标记时进场景,标记后不进
    assert axes_to_scene(got["axes"], _transform()) != {"x": [], "y": []}
    assert axes_to_scene(got["axes"], _transform(),
                         suspect=True) == {"x": [], "y": []}


@pytest.mark.unit
def test_suspect_warning_survives_to_the_result():
    """警告必须真的留在结果里。

    **这条防的是我刚犯过的错**:警告先 append 进 `result["warnings"]`,
    而函数末尾又 `result["warnings"] = warnings` 整体赋值,
    先写的被原样覆盖掉 —— 标记还在,人却看不到原因。
    """
    from services import axis_recognition as ar

    original = ar.SYMBOL_FIELD_BAND_HINT
    try:
        ar.SYMBOL_FIELD_BAND_HINT = 0        # 让任何图都触发标记
        got = recognize(CIRCLES, strokes=[], segments=[], page_w=PAGE_W,
                        page_h=PAGE_H, read_text=lambda leader: [])
    finally:
        ar.SYMBOL_FIELD_BAND_HINT = original
    assert got["suspect_symbol_field"] is True
    assert any("符号场" in w for w in got["warnings"]), got["warnings"]
    assert got["axis_count"] > 0, "标记不等于拦截 —— 轴线仍要产出"


@pytest.mark.unit
def test_band_hint_leaves_room_above_measured_real_grids():
    """阈值要**离真轴网远**。实测真轴网最多 42 条带(A-10-04C 一层平面)。"""
    assert SYMBOL_FIELD_BAND_HINT >= 60


@pytest.mark.unit
def test_suspect_drawings_skip_the_expensive_ocr_pass():
    """可疑图不做世界锚点(最贵的一段:逐引线裁图 + OCR)。

    **实测代价**:旧闸让 451 张图直接短路返回;改成「只标记不拦截」后,
    它们要跑完整链路,单张耗时 **111 秒**,全项目重跑估算 **35 小时**。
    而这些图的锚点根本不会被消费——建模侧已按
    `suspect_symbol_field = false` 过滤(migration 042)。
    既然不消费,就不该算。

    轴线本身仍要产出(留档可查),只跳过锚点这一段。
    """
    from services import axis_recognition as ar

    calls = []

    def _spy_read(leader):
        calls.append(leader)
        return []

    original = ar.SYMBOL_FIELD_BAND_HINT
    tip = (1000.0, 1900.0)
    joint = (tip[0] - 45.0, tip[1] - 45.0)
    segments = [(joint[0] - 94.0, joint[1], joint[0], joint[1]),
                (joint[0], joint[1], tip[0], tip[1])]
    try:
        ar.SYMBOL_FIELD_BAND_HINT = 0          # 强制标记为可疑
        got = recognize(CIRCLES, strokes=[], segments=segments, page_w=PAGE_W,
                        page_h=PAGE_H, read_text=_spy_read)
    finally:
        ar.SYMBOL_FIELD_BAND_HINT = original

    assert got["suspect_symbol_field"] is True
    assert got["axis_count"] > 0, "轴线仍要产出 —— 跳过的只是锚点"
    assert calls == [], "可疑图不应触发一次 OCR"
    assert got["anchors"] == []


@pytest.mark.unit
def test_normal_drawings_still_run_the_ocr_pass():
    """正常图不受影响。"""
    calls = []
    tip = (1000.0, 1900.0)
    joint = (tip[0] - 45.0, tip[1] - 45.0)
    segments = [(joint[0] - 94.0, joint[1], joint[0], joint[1]),
                (joint[0], joint[1], tip[0], tip[1])]
    got = recognize(CIRCLES, strokes=[], segments=segments, page_w=PAGE_W,
                    page_h=PAGE_H,
                    read_text=lambda leader: calls.append(leader) or [])
    assert got["suspect_symbol_field"] is False
    assert calls, "正常图必须照常读 OCR"
