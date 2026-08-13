"""未分层图要说清**为什么判不出** —— 否则人工队列是一堆没头绪的图。

**用户口径**(J7 设计答案 2):无法定位的图在图纸管理里给出**标签分类**
→ 人工处理(或人工补充说明)→ 再次系统处理 → **循环直至处理完毕**。

人工通道早就有(`drawing_model_annotations` 可写 `story_key`/`building_unit_key`),
缺的是**分类**:队列里 1061 张,人打开看不出每张为什么判不出、该补什么。

**实测的三类**(有世界坐标却未入层的 3 张):

| 图 | 判不出的原因 |
|---|---|
| `A-01-04A 竖向结构定位图` | **跨楼层** —— 它本就不属于单一楼层 |
| `A-10-01.1C 大歌剧厅台仓平面图` | **非标准楼层名** —— 台仓是舞台下方空间 |
| `A-11-01.1B 台仓隔声隔振平面图` | 同上 |

三类要分开,因为**人的动作不同**:
跨楼层图**不该**硬指定楼层(指定了反而错);
非标准楼层名只需人告诉系统「台仓 ≈ 哪一层」;
毫无线索的才需要人翻图。
"""
from __future__ import annotations

import pytest

from services.unzoned_reason import (
    REASON_CROSS_FLOOR, REASON_NO_FLOOR_BY_NATURE, REASON_NON_STANDARD_NAME,
    REASON_NO_HINT, classify_unzoned,
)


@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "建筑-竣工图--竖向结构定位图",
    "结构-竣工图--竖向构件定位图",
    "建筑-竣工图--立面分格图",
])
def test_cross_floor_drawings_are_labelled(title):
    """**跨楼层图不该硬指定楼层** —— 指定了反而是错的。"""
    got = classify_unzoned({"title": title})
    assert got.reason == REASON_CROSS_FLOOR
    assert not got.needs_floor_input, "跨楼层图不该要人填楼层"


@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "建筑-竣工图--大歌剧厅台仓平面图",
    "建筑-竣工图--台仓隔声隔振平面图",
])
def test_non_standard_floor_names_are_labelled(title):
    """**台仓**是舞台下方空间,不是 F1/B1 这样的标准楼层名。

    人只需告诉系统「台仓 ≈ 哪一层」,不必翻图。
    """
    got = classify_unzoned({"title": title})
    assert got.reason == REASON_NON_STANDARD_NAME
    assert got.needs_floor_input
    assert got.hint, "要把识别到的非标准名回显给人看"


@pytest.mark.unit
def test_drawing_without_any_hint():
    # 用一张**该有楼层却看不出**的图 —— 「详图」本就无楼层，属 by_nature。
    got = classify_unzoned({"title": "建筑-竣工图--墙身构造", "drawing_no": "A-99-01"})
    assert got.reason == REASON_NO_HINT
    assert got.needs_floor_input


@pytest.mark.unit
def test_reason_carries_a_human_readable_action():
    """每类都要说清**人该做什么** —— 这是队列的价值所在。"""
    for title in ("竖向结构定位图", "台仓平面图", "详图"):
        got = classify_unzoned({"title": title})
        assert got.action, f"{title} 缺少建议动作"


@pytest.mark.unit
def test_empty_input_is_safe():
    got = classify_unzoned({})
    assert got.reason == REASON_NO_HINT
    assert classify_unzoned(None).reason == REASON_NO_HINT


@pytest.mark.unit
def test_drawing_no_is_also_checked():
    got = classify_unzoned({"title": "", "drawing_no": "台仓平面图-01"})
    assert got.reason == REASON_NON_STANDARD_NAME


@pytest.mark.unit
def test_result_is_serialisable():
    """要能进 scene / API 载荷。"""
    got = classify_unzoned({"title": "竖向结构定位图"})
    payload = got.as_dict()
    assert set(payload) >= {"reason", "action", "needs_floor_input"}
    assert isinstance(payload["needs_floor_input"], bool)


# ── 本就无楼层的图不该进人工队列(实测 93.6% 落在兜底类)──────────

@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "01施工总说明-dq-总说明",
    "给排水-竣工图--消火栓系统原理图(一)",
    "电气-竣工图--配电箱系统图",
    "建筑-竣工图--图纸目录",
])
def test_drawings_without_a_floor_by_nature_are_separated(title):
    """**「本就没有」与「该有却没有」必须分开**。

    实测未分层 1061 张里 **93.6% 落在兜底类**「毫无线索」,
    而队列第一条是「01施工总说明」—— 它本就不该有楼层。

    这是 `building_unit_fallback` 那轮的教训:当时原报「1866 张未分配」,
    拆开后 959 张**本就无单体归属**,真正需要处理的只有 907 张(**虚高 2.1 倍**)。
    混在一起报,会让人去处理一个不存在的问题。

    判据复用 `drawing_role`(国标术语,不绑编号体系)。
    """
    got = classify_unzoned({"title": title})
    assert got.reason == REASON_NO_FLOOR_BY_NATURE
    assert not got.needs_floor_input, "本就无楼层的图不该要人填"


@pytest.mark.unit
def test_plans_are_not_treated_as_floorless():
    """平面图**该有**楼层 —— 判不出就是真的要人处理,不能借这一类掩盖。"""
    got = classify_unzoned({"title": "建筑-竣工图--某某平面图"})
    assert got.reason != REASON_NO_FLOOR_BY_NATURE
    assert got.needs_floor_input


@pytest.mark.unit
def test_cross_floor_still_wins_over_by_nature():
    """跨层判据优先 —— 竖向定位图是几何图,但确实跨层。"""
    got = classify_unzoned({"title": "建筑-竣工图--竖向结构定位图"})
    assert got.reason == REASON_CROSS_FLOOR
