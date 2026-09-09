"""梁配筋图不产出柱（N1-③ 的前置修复）。

**为什么**：准备重建存量场景时抽样对比，发现两张「主梁配筋图」的柱数从
0 涨到 218 和 320。诊断后确认**不是 bug** —— 那些柱来自图层
`S-北区-F3-COLS$0$0S-COLS-HATCH`（`COLS` 就是柱图层），主梁配筋图上确实
画着柱截面，因为柱是梁的支座。它们是 `c67e512`「`填充`/`HATCH` 豁免保住
构件填充截面」的预期结果。

**但它们不该进模型**：实测同一楼层**同时**选用两类图（歌剧院地下一层
23 张结构平面 + 14 张梁配筋，1 层 13 + 14），而聚合**没有跨图纸去重**
（去重只覆盖轴线与圆检测补柱），且 `placed_drawings=0` 没有世界坐标，
空间上也判不出重合。于是同一根柱被算两次。

**判据与既有范式同构**：`is_beam_drawing_effective` 早就立过「图名对图种
有否决权」—— 墙配筋图上的平行线对是墙不是梁。对称地：梁配筋图的**主题
是梁的配筋**，图上的柱是参照物，不是这张图要表达的构件。

**关掉的风险面实测为零**：全库没有任何一层只靠配筋图提供柱
（同层必有结构平面图/墙柱平面图/柱平面图）。
"""
from __future__ import annotations

import pytest

from core.model3d.element_recognizer import is_rebar_drawing_for_beams


@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "结构-竣工图--北区（小歌剧厅）三层主梁配筋图",
    "结构-竣工图--南区（大、中歌剧厅）地下一层主梁配筋图（三）",
    "三层次梁配筋图",
    "梁平法施工图（配筋）",
])
def test_beam_rebar_drawings_are_recognized(title):
    """梁配筋图认得出来 —— 判据取图名里同时出现「梁」与「配筋」。"""
    assert is_rebar_drawing_for_beams(title) is True


@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "结构-竣工图--南区（大、中歌剧厅）一层结构平面图（四）",
    "结构-竣工图--北区（小歌剧厅）三层墙柱平面图",
    "柱平法施工图",
    "板配筋图",          # 板的配筋图不是梁配筋图
    "墙身大样",
    "",
    None,
])
def test_other_drawings_are_not_gated(title):
    """柱的正常来源一律不受影响 —— 这条闸只针对梁配筋图。

    「板配筋图」特意列在这里：判据要求**同时**出现「梁」与「配筋」，
    只看「配筋」会把板配筋图也关掉，而那不是本闸要管的。
    """
    assert is_rebar_drawing_for_beams(title) is False


@pytest.mark.unit
def test_beam_rebar_drawing_yields_beams_but_no_columns():
    """走一遍识别器：梁配筋图上梁照出、柱不出。

    构造一张图上同时有柱截面填充（柱图层）与梁的平行线对，断言
    柱被关掉而梁不受影响 —— 关错了会把这类图的主产物也丢掉。
    """
    from core.model3d import DrawingGeometry, recognize
    from core.model3d.element_recognizer import SCALE_1_100_M_PER_PT

    pt_per_m = 1.0 / SCALE_1_100_M_PER_PT

    def _build(title: str):
        geom = DrawingGeometry(page_w=842.0, page_h=595.0)
        # 柱截面填充：0.6×0.6m，落在柱图层上
        for cx in (200.0, 400.0):
            hw = 0.6 * pt_per_m / 2
            geom.polys.append([(cx - hw, 300 - hw), (cx + hw, 300 - hw),
                               (cx + hw, 300 + hw), (cx - hw, 300 + hw)])
            geom.poly_layers.append("S-COLS-HATCH")
            geom.poly_blocks.append("")
        geom.texts.append((400.0, 20.0, title))
        return geom

    gated = recognize(_build("三层主梁配筋图"), "structure", "d-rebar",
                      drawing_title="三层主梁配筋图", view_type="plan")
    normal = recognize(_build("三层结构平面图"), "structure", "d-plan",
                       drawing_title="三层结构平面图", view_type="plan")

    assert len(normal.columns) == 2, (
        f"对照组：结构平面图上这两个柱截面应被识别，实得 {len(normal.columns)}")
    assert len(gated.columns) == 0, (
        f"梁配筋图不该产出柱，实得 {len(gated.columns)}")


@pytest.mark.unit
def test_gate_is_visible_not_silent():
    """关掉一整条路径要留痕（`MODELING_PIPELINE_BLUEPRINT.md` §7 降级必须可见）。"""
    import logging

    from core.model3d import DrawingGeometry, recognize

    geom = DrawingGeometry(page_w=842.0, page_h=595.0)
    geom.texts.append((400.0, 20.0, "三层主梁配筋图"))
    logger = logging.getLogger("core.model3d.element_recognizer")
    records: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r.getMessage())  # type: ignore[method-assign]
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        recognize(geom, "structure", "d-rebar",
                  drawing_title="三层主梁配筋图", view_type="plan")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    assert any("配筋" in m for m in records), (
        f"关闭柱产出要写日志，实得 {records}")
