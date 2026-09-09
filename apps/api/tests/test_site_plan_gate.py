"""总平面图不产出构件（N1-③ 重建后发现的回归）。

**怎么发现的**：第二工程重建后柱 4460 → 4602（+3.2%），逐图诊断发现净增
几乎全来自一张「周边环境总平面图」—— 柱 133 → 459（**+326**），
而其余图大多在下降（−69/−50/−45/−28…，本轮的闸在正常工作）。

**根因链**（每一环都能核对）：

1. 该图比例被算成 **0.4 m/pt ≈ 1:1134** —— 不在 GB/T 50001 §6.0.4 表内；
2. 按这个比例，图上 4pt 的景观符号（树、灯、井盖）换算成 **1.6 米**；
3. 1.6m 的近正方形落进柱窗口（实测尺寸最多的是 1.63×1.63、1.25×1.25）；
4. 系统**已经标了** `scale_suspect=true`（459 根全带），但可疑构件仍进模型。

**为什么不从第 4 步下手**：全工程 `scale_suspect=true` 的占比是
墙 60.7%、板 46.9%、柱 35.4%、管 31.1% —— 一刀切关掉会让墙少六成，
那是另一个量级的决策，不该顺手做。

**从图种下手**：总平面图画的是**场地关系**（道路、绿化、建筑轮廓、
用地红线），不画构件截面。这与 `is_rebar_drawing_for_beams`、
`is_beam_drawing_effective` 同属一条规则：**图名对图种有否决权**。
全库仅 12 张总平面图，范围可控。
"""
from __future__ import annotations

import pytest

from core.model3d.element_recognizer import is_site_plan


@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "周边环境总平面图",
    "总平面图",
    "建筑总平面图（一）",
    "1-1 区总平面定位图",
])
def test_site_plans_are_recognized(title):
    assert is_site_plan(title) is True


@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "结构-竣工图--南区（大、中歌剧厅）一层结构平面图（四）",
    "二层防火分区平面图",
    "地下二层结构平面图",
    "一层结构平面总图",     # 「平面总图」不是「总平面图」
    "九层平面布置图",
    "",
    None,
])
def test_ordinary_plans_are_not_gated(title):
    """普通平面图一律不受影响。

    「一层结构平面总图」特意列在这里：它是**某一层的**平面图，
    只是叫「总图」；判据必须认「总平面」这个连续词，不能拆开匹配。
    """
    assert is_site_plan(title) is False


@pytest.mark.unit
def test_site_plan_yields_no_columns_or_walls():
    """走一遍识别器：总平面图上的近方块不再变成柱。

    构造按实测尺寸来 —— 1.63×1.63m 的近正方形，正是那张图上
    数量最多的那一档（111 个）。
    """
    from core.model3d import DrawingGeometry, recognize
    from core.model3d.element_recognizer import SCALE_1_100_M_PER_PT

    pt_per_m = 1.0 / SCALE_1_100_M_PER_PT

    def _build(title: str):
        geom = DrawingGeometry(page_w=842.0, page_h=595.0)
        for i in range(4):
            hw = 1.63 * pt_per_m / 2
            cx = 150.0 + i * 120.0
            geom.polys.append([(cx - hw, 300 - hw), (cx + hw, 300 - hw),
                               (cx + hw, 300 + hw), (cx - hw, 300 + hw)])
            geom.poly_layers.append("S-COLU")
            geom.poly_blocks.append("")
        geom.texts.append((400.0, 20.0, title))
        return geom

    gated = recognize(_build("周边环境总平面图"), "structure", "d-site",
                      drawing_title="周边环境总平面图", view_type="plan")
    normal = recognize(_build("三层结构平面图"), "structure", "d-plan",
                       drawing_title="三层结构平面图", view_type="plan")

    assert len(normal.columns) == 4, (
        f"对照组：普通平面图上这些方块应被识别，实得 {len(normal.columns)}")
    assert len(gated.columns) == 0, f"总平面图不该产柱，实得 {len(gated.columns)}"


@pytest.mark.unit
def test_gate_is_visible_not_silent():
    """关掉一整张图的构件产出必须留痕（降级必须可见）。"""
    import logging

    from core.model3d import DrawingGeometry, recognize

    geom = DrawingGeometry(page_w=842.0, page_h=595.0)
    geom.texts.append((400.0, 20.0, "周边环境总平面图"))
    logger = logging.getLogger("core.model3d.element_recognizer")
    records: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r.getMessage())  # type: ignore[method-assign]
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        recognize(geom, "structure", "d-site",
                  drawing_title="周边环境总平面图", view_type="plan")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)

    assert any("总平面" in m for m in records), f"要写日志，实得 {records}"
