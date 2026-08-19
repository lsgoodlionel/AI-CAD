"""墙配筋图上的填充截面是**墙**,不是柱 —— 实测 1404 根假柱。

**发现经过**:柱数从 7052 涨到 10796 后追查 F5 层的 1921 根柱,
按来源图分组:

| 来源图 | 柱数 |
|---|---:|
| 南区（大、中歌剧厅）四~五层中歌剧厅**墙**配筋平面图 | **1337** |
| 南区（大、中歌剧厅）四夹~五层**柱**平面图 | 453 |
| 南区（大、中歌剧厅）四夹层~五层**墙**配筋平面图 | **67** |

**1404 根「柱」来自墙配筋图** —— 墙的截面填充多边形尺寸落在柱的
判据范围内,就被判成了柱。

这与「梁图上的平行线对被当成墙」是同一类问题的镜像:
**图名明确声明了图种,几何判据却没听**。
处置沿用同一条原则:**图种声明优先于几何猜测**。
"""
from __future__ import annotations

import pytest

from core.model3d.element_recognizer import is_wall_drawing


@pytest.mark.unit
def test_wall_reinforcement_drawing_is_recognised():
    """**核心用例**:图名声明是墙配筋图。"""
    for title in ("四~五层中歌剧厅墙配筋平面图",
                  "四夹层~五层墙配筋平面图",
                  "地下一层剪力墙平面布置图"):
        assert is_wall_drawing(title), title


@pytest.mark.unit
def test_column_drawing_is_not_a_wall_drawing():
    """**不得误伤柱图** —— 它们本就该产出柱。"""
    for title in ("四夹~五层柱平面图", "柱配筋详图（四）",
                  "首层框架柱平面布置图"):
        assert not is_wall_drawing(title), title


@pytest.mark.unit
def test_drawing_with_both_words_prefers_the_leading_one():
    """**图名同时含墙与柱时按先出现者** —— 图名的主语在前。

    「墙柱平面图」主语是墙柱共同表达,而「柱墙节点」以柱为主。
    这个判据不完美,但比一律归柱强,且**判错只影响归类不丢构件**。
    """
    assert is_wall_drawing("墙柱配筋平面图")
    assert not is_wall_drawing("柱墙连接节点详图")


@pytest.mark.unit
def test_plain_plan_is_not_a_wall_drawing():
    """普通平面图不做假定 —— 判不出就走原路径。"""
    assert not is_wall_drawing("五层（设备层）平面图")
    assert not is_wall_drawing("")
    assert not is_wall_drawing(None)


@pytest.mark.unit
def test_wall_drawing_suppresses_geometric_columns():
    """**接线用例**:墙图上的填充截面不再产出柱。

    仍保留**图层明确为柱**的路径 —— 那是设计师的明确标注,
    比图名更强(墙图上确实可能画几根柱)。
    """
    from core.model3d.element_recognizer import recognize
    from core.model3d.types import DrawingGeometry

    geom = DrawingGeometry(page_w=1000.0, page_h=1000.0)
    # 一个尺寸落在柱判据内的填充多边形
    for i in range(3):
        geom.polys.append([(i * 50.0, 0.0), (i * 50.0 + 12.0, 0.0),
                           (i * 50.0 + 12.0, 12.0), (i * 50.0, 12.0)])
        geom.poly_layers.append("")
        geom.poly_blocks.append("")
    geom.texts.append((0.0, 0.0, "四~五层中歌剧厅墙配筋平面图"))

    with_title = recognize(geom, "structure", "d1",
                           drawing_title="四~五层中歌剧厅墙配筋平面图")
    without = recognize(geom, "structure", "d1")
    assert len(with_title.columns) < len(without.columns) or not without.columns


# ── 墙图上的平行线对归墙，不归梁 ────────────────────────────────

@pytest.mark.unit
def test_wall_drawing_beats_beam_heuristic():
    """**实测缺陷**:「三~四层大歌剧厅**墙**配筋平面图」
    `is_wall_drawing=True`,却仍产出 18 根梁、**0 面墙** ——
    整层 F4 因此墙 0 梁 186。

    根因:我上一版加了 `is_wall_drawing`,**只用在柱识别**,
    没用在墙/梁分流。图名声明是墙图时,平行线对必须归墙。

    这与「梁图上的平行线对归梁」是同一条规则的另一半 ——
    **图种声明优先于几何猜测**,两个方向都要成立。
    """
    from core.model3d.element_recognizer import is_beam_drawing_effective

    # 图名说是墙图 → 无论几何/图层多像梁，都不按梁图处理
    assert not is_beam_drawing_effective(
        beam_like=True, drawing_title="三~四层大歌剧厅墙配筋平面图")
    # 图名说是梁图 → 照常
    assert is_beam_drawing_effective(
        beam_like=True, drawing_title="四层梁配筋平面图")
    # 图名两不沾 → 由几何判据决定
    assert is_beam_drawing_effective(beam_like=True, drawing_title="四层平面图")
    assert not is_beam_drawing_effective(beam_like=False, drawing_title="四层平面图")


@pytest.mark.unit
def test_title_can_also_assert_a_beam_drawing():
    """**图名有双向发言权** —— 否决是一半,认定是另一半。

    实测「地下一层**主梁**配筋图（四）」产出 **516 面墙、0 根梁**:
    `_is_beam_drawing` 只看**图内文字**,而大歌剧院矢量文字常取不到,
    于是图名白纸黑字写着「主梁配筋图」却没判成梁图。

    对称地:图名含「梁」且「梁」在「墙」之前 → 认定为梁图。
    """
    from core.model3d.element_recognizer import is_beam_drawing_effective

    # 几何判不出，但图名说是梁图 → 认定
    assert is_beam_drawing_effective(
        beam_like=False, drawing_title="地下一层主梁配筋图（四）")
    assert is_beam_drawing_effective(
        beam_like=False, drawing_title="三层次梁平法施工图")
    # 墙在前 → 仍是墙图，不因含「梁」翻盘
    assert not is_beam_drawing_effective(
        beam_like=False, drawing_title="墙梁配筋平面图")
    # 两不沾且几何判不出 → 不认定
    assert not is_beam_drawing_effective(
        beam_like=False, drawing_title="四层平面图")
