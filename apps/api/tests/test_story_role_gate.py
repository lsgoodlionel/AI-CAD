"""按建模角色拦截「本就不该有层」的图纸。

实测逼出来的：80 张图独立判读，**19 张系统指派了楼层但那张图本就不该有层**
（其中图名与判读一致认定的确凿错误 12 张 = 15%）。哨兵层更差 ——
错 43%，是常规层 20% 的两倍；`order=101` 的唯一一张是「正压系统原理图」。

而 `drawing_role` 其实**已经认识其中一部分**，只是从未接进楼层归属：

    判读说不该有层的 19 张   non_geometric 4 · detail 3 · coordinate_base 1
    判读说该有层的   61 张   这三个角色出现 **0 次**

所以按这三个角色拦截，在该样本上删掉 8/19 错误、**误伤为零**。
"""
import pytest

from services.drawing_role import (
    ROLE_COMPONENT_SOURCE, ROLE_COORDINATE_BASE, ROLE_DETAIL,
    ROLE_FLOOR_SKELETON, ROLE_NON_GEOMETRIC, ROLE_UNKNOWN,
)
from services.model_story import NON_FLOOR_ROLES, normalize_story_table


def _drawing(did, title, no=""):
    return {"id": did, "title": title, "drawing_no": no, "discipline": "structure"}


@pytest.mark.parametrize("role", [ROLE_NON_GEOMETRIC, ROLE_DETAIL, ROLE_COORDINATE_BASE])
def test_这三个角色不参与楼层归属(role):
    assert role in NON_FLOOR_ROLES


@pytest.mark.parametrize("role", [ROLE_COMPONENT_SOURCE, ROLE_FLOOR_SKELETON,
                                  ROLE_UNKNOWN])
def test_其余角色照旧参与(role):
    """`unknown` 也照旧参与 —— 判不出不等于不该有层，宁可保留。"""
    assert role not in NON_FLOOR_ROLES


def test_系统原理图不再被指派楼层():
    """实测实例：`正压系统原理图（三）` 曾被塞进 order=101（高屋面）。"""
    r = normalize_story_table([_drawing("d1", "正压系统原理图（三）")])
    assert [u["drawing_id"] for u in r.unclassified_drawings] == ["d1"]
    assert r.drawing_assignments["d1"]["story_role_excluded"] is True


def test_说明性图纸不再被指派楼层():
    """实测实例：`结构施工图设计统一说明（十三）` 曾被指派到 order=4。"""
    r = normalize_story_table([_drawing("d1", "结构施工图设计统一说明（十三）")])
    assert r.drawing_assignments["d1"]["story_role_excluded"] is True


def test_判不出角色的图仍会被指派楼层_这是已知残留():
    """`桩基说明` 曾被塞进 order=-98，但 `classify_role` 判它 `unknown`
    （置信 0.2），而 `unknown` 按设计**不拦**——判不出不等于不该有层。

    19 张实测错误里有 4 张是这一类，角色闸覆盖不到。如实留证，
    不为了让数字好看去扩大拦截面。"""
    from services.drawing_role import ROLE_UNKNOWN, classify_role

    assert classify_role({"title": "桩基说明", "discipline": "structure",
                          "drawing_no": ""}).role == ROLE_UNKNOWN
    r = normalize_story_table([_drawing("d1", "桩基说明")])
    assert not r.drawing_assignments["d1"].get("story_role_excluded")


def test_平面图照常归层():
    r = normalize_story_table([_drawing("d1", "三层结构平面图")])
    assert r.drawing_assignments["d1"]["story_order"] == 3
    assert not r.drawing_assignments["d1"].get("story_role_excluded")


def test_拦截原因写进质量问题里可见():
    """降级必须可见（蓝图 §7 约束 3）—— 不能悄悄把图丢掉。"""
    r = normalize_story_table([_drawing("d1", "正压系统原理图（三）")])
    assert any(i.issue_type == "story_role_excluded" for i in r.issues)


def test_被排除的图仍会落进未分层_这是既有行为():
    """被排除的图落进「未分层」桶，于是场景里出现一个全零的空层。

    **这是所有未分类图纸的既有行为**，不是角色闸引入的 ——
    实测第二工程场景本来就有一个 `order=0` 且挂 0 张图的空层。
    在 builder 侧跳过它会让下游按 `floor_of[drawing_id]` 取值 KeyError，
    波及面超出本次改动，故留作独立待办，不在这里顺手改。
    """
    r = normalize_story_table([_drawing("d1", "正压系统原理图（三）")])
    a = r.drawing_assignments["d1"]
    assert a["story_role_excluded"] is True
    assert a["story_key"]          # 仍落在未分层桶里，没有凭空消失
