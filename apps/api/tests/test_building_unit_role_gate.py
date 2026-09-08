"""按建模角色拦截「本就不该有单体」的图纸 —— 与楼层归属同一个病、同一副药。

实测逼出来的：80 张整图缩略独立判读（`data/model3d/gold/building_unit_v1.json`），
系统给**每一张图**都指派了一个单体：

    判读 scope   not_spatial 43 · whole_site 15 · one_building 13
                 partial 7 · multi_building 2

    **50/80 = 62% 的图被指派了单体，却根本没有空间范围或只画了一部分**

「metro 侧 26 张全是 main」不是因为地铁只有一个单体，是因为其中 **19 张
判读为 `not_spatial`** —— 多数图本就不该有单体。

而上一轮为楼层归属做的 `NON_FLOOR_ROLES` 用在这里，实测：

    能删 31/50 张错误归属，误伤 1/30 张正确的（3.3%）

判读说「不该有单体」的 50 张里：detail 20 · non_geometric 11 ·
component_source 9 · unknown 7 · elevation_reference 3 ——
可拦的正是 detail + non_geometric 这 31 张。
"""
import pytest

from services.drawing_role import (
    ROLE_COMPONENT_SOURCE, ROLE_COORDINATE_BASE, ROLE_DETAIL,
    ROLE_FLOOR_SKELETON, ROLE_NON_GEOMETRIC, ROLE_UNKNOWN,
)
from services.model_story import (
    NON_FLOOR_ROLES, NON_UNIT_ROLES, normalize_story_table,
)


def _drawing(did, title, no=""):
    return {"id": did, "title": title, "drawing_no": no, "discipline": "structure"}


def _unit_keys(result):
    return {item["unit_key"] for item in result.building_units}


# ── 闸门本身 ──────────────────────────────────────────────────

def test_单体闸与楼层闸同一副药_单一来源():
    """两个闸用同一个角色集合。写两遍必然漂移。"""
    assert NON_UNIT_ROLES is NON_FLOOR_ROLES


@pytest.mark.parametrize("role", [ROLE_NON_GEOMETRIC, ROLE_DETAIL, ROLE_COORDINATE_BASE])
def test_这三个角色不参与单体归属(role):
    assert role in NON_UNIT_ROLES


@pytest.mark.parametrize("role", [ROLE_COMPONENT_SOURCE, ROLE_FLOOR_SKELETON,
                                  ROLE_UNKNOWN])
def test_其余角色照旧参与单体归属(role):
    """`unknown` 也照旧参与 —— 判不出不等于不该有单体（蓝图 §7 约束 5）。"""
    assert role not in NON_UNIT_ROLES


# ── 幻影单体：只由被排除的图撑起来的单体，整个消失 ──────────────

def test_节点大样不再凭空造出一个南区():
    """实测：`南区大歌剧厅屋面节点大样` 被指派 unit=south（源 title）。

    详图画的是通用做法、跨单体复用，它一张图就能在「已识别单体」列表里
    立起一个 `south` —— 而这个 south 一层楼都没有。
    """
    r = normalize_story_table([_drawing("d1", "南区大歌剧厅屋面节点大样")])
    assert _unit_keys(r) == set()
    assert r.stories_by_building == {}


def test_轴网定位图不再凭空造出一个北区():
    """实测：`北区轴网定位图` role=coordinate_base 却被指派 unit=north。"""
    r = normalize_story_table([_drawing("d1", "北区轴网定位图")])
    assert _unit_keys(r) == set()


def test_说明性图纸不再撑起一个main():
    r = normalize_story_table([_drawing("d1", "结构施工图设计统一说明（十三）")])
    assert _unit_keys(r) == set()


# ── 不误伤：有空间意义的图照旧归属 ────────────────────────────

def test_平面图照常归属单体():
    r = normalize_story_table([_drawing("d1", "南区（大歌剧厅）三层结构平面图")])
    assert _unit_keys(r) == {"south"}
    assert r.drawing_assignments["d1"]["building_unit_key"] == "south"
    assert not r.drawing_assignments["d1"].get("building_unit_role_excluded")


def test_判不出角色的图仍会被指派单体_这是已知残留():
    """`桩基说明` 置信仅 0.2 判 `unknown`，按设计**不拦**。

    判读说不该有单体的 50 张里有 7 张正是 `unknown`，角色闸覆盖不到。
    如实留证，不为了让数字好看去扩大拦截面。
    """
    from services.drawing_role import ROLE_UNKNOWN, classify_role

    assert classify_role({"title": "桩基说明", "discipline": "structure",
                          "drawing_no": ""}).role == ROLE_UNKNOWN
    r = normalize_story_table([_drawing("d1", "桩基说明")])
    assert _unit_keys(r) == {"main"}


def test_同一单体只要还有一张有空间意义的图就保留():
    """误伤 1/30 说的是单张图，不是整个单体 —— 单体由多张图共同撑起。"""
    r = normalize_story_table([
        _drawing("d1", "南区大歌剧厅屋面节点大样"),
        _drawing("d2", "南区（大歌剧厅）三层结构平面图"),
    ])
    assert _unit_keys(r) == {"south"}


# ── 人审在环：人工指定的单体压过闸门 ──────────────────────────

def test_人工指定的单体不被闸门推翻():
    """人已经说了这张详图属于南区，系统不能反过来抹掉它（E1.5 人审在环）。"""
    r = normalize_story_table(
        [_drawing("d1", "南区大歌剧厅屋面节点大样")],
        annotations={"d1": {"building_unit_key": "south",
                            "building_unit_display_name": "南区"}},
    )
    assert _unit_keys(r) == {"south"}
    assert not r.drawing_assignments["d1"].get("building_unit_role_excluded")


# ── 降级必须可见（蓝图 §7 约束 3）────────────────────────────

def test_拦截原因写进质量问题里可见():
    r = normalize_story_table([_drawing("d1", "南区大歌剧厅屋面节点大样")])
    issues = [i for i in r.issues if i.issue_type == "building_unit_role_excluded"]
    assert len(issues) == 1
    # 被拿掉的那个单体键要留在问题里，否则无从追查「南区去哪了」
    assert issues[0].building_unit_key == "south"
    assert issues[0].drawing_id == "d1"


def test_拦截事实写在图纸归属上可见():
    r = normalize_story_table([_drawing("d1", "南区大歌剧厅屋面节点大样")])
    assert r.drawing_assignments["d1"]["building_unit_role_excluded"] is True


def test_被排除的图仍进人工标注队列_不悄悄丢():
    r = normalize_story_table([_drawing("d1", "南区大歌剧厅屋面节点大样")])
    assert [u["drawing_id"] for u in r.unclassified_drawings] == ["d1"]


# ── 边界：本次不动的部分，写明而不是假装没有 ──────────────────

def test_图纸归属上仍留着原单体键_这是本次刻意不动的边界():
    """闸门只拦「聚合出的单体列表」，**不清空单张图的 `building_unit_key`**。

    清空它会让下游 `model_elements.building_of` 走回退分支**重新识别一遍**
    （回退里直接调 `detect_building_unit`），等于闸门被静默撤销。

    **这条边界的范围已经变小了**：上游把「未分类图纸造出空幻影层」修掉之后
    （`_build_floors` 不再产出 `UNZONED` 层），被排除的图不进任何楼层，
    于是 `group_buildings` 根本够不到它们。但 `_marker_building_keys` 仍对
    **全部**图纸调 `building_of`，静默撤销在那条路上依然成立。

    要真正落地得给这些图一个专门的归属桶，并让 `building_of` 认这个标记 ——
    是独立一步，且**应当先有实测**（本轮 80 张样本量的是聚合层，没量过
    标记桶对 markers 的影响）。故此处如实留痕：键还在、
    `building_unit_role_excluded` 标着，3D 分组行为本次不变。
    """
    r = normalize_story_table([_drawing("d1", "南区大歌剧厅屋面节点大样")])
    a = r.drawing_assignments["d1"]
    assert a["building_unit_key"] == "south"      # 没有凭空消失
    assert a["building_unit_role_excluded"] is True
