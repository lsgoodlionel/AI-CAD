"""A-16 图层约定强化识别：修「柱必须 filled」漏检 + 图层/块名识别。

无图层信息时行为与原启发式一致（由 test_element_recognizer 覆盖 + 本文件对照用例）。
"""
import pytest

from core.model3d import DrawingGeometry, FloorElements, recognize
from core.model3d.element_recognizer import SCALE_1_100_M_PER_PT

PAGE_W, PAGE_H = 842.0, 595.0
PT_PER_M = 1.0 / SCALE_1_100_M_PER_PT


def _plan_unfilled_columns(layer: str) -> DrawingGeometry:
    """轴网 + 4 根【未填充】0.6m 柱矩形，图层=layer（并行列表对齐 append）。"""
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    ox, oy = 100.0, 100.0
    span = 8.4 * PT_PER_M
    for i in range(3):
        x = ox + i * span
        geom.lines.append((x, 30.0, x, PAGE_H - 30.0))
        geom.line_layers.append("AXIS")
        y = oy + i * span * 0.5
        geom.lines.append((30.0, y, PAGE_W - 30.0, y))
        geom.line_layers.append("AXIS")
    col = 0.6 * PT_PER_M
    for i in range(2):
        for j in range(2):
            geom.rects.append(
                (ox + i * span - col / 2, oy + j * span * 0.5 - col / 2, col, col, False)
            )
            geom.rect_layers.append(layer)
            geom.rect_blocks.append("")
    geom.texts.append((60.0, 40.0, "1:100"))
    geom.texts.append((400.0, 20.0, "一层柱结构平面图"))
    return geom


@pytest.mark.unit
def test_unfilled_columns_recognized_via_layer():
    """S-COLU 图层的未填充柱矩形被识别（修复 filled 漏检）。"""
    result = recognize(_plan_unfilled_columns("S-COLU"), "structure", "d1")
    assert isinstance(result, FloorElements)
    assert len(result.columns) == 4


@pytest.mark.unit
def test_unfilled_columns_missed_without_layer():
    """无图层信息时未填充矩形按原启发式跳过（零回归对照）。"""
    result = recognize(_plan_unfilled_columns(""), "structure", "d1")
    assert result.columns == []


@pytest.mark.unit
def test_equipment_recognized_via_layer_block():
    """机电图：超尺寸阈值的具名设备块靠图层 M-EQPM 识别为设备。"""
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    for x in (120.0, 360.0):
        geom.lines.append((x, 30.0, x, PAGE_H - 30.0))
        geom.line_layers.append("AXIS")
    for y in (120.0, 360.0):
        geom.lines.append((30.0, y, PAGE_W - 30.0, y))
        geom.line_layers.append("AXIS")
    big = 6.0 * PT_PER_M  # 超出 0.5~5m 设备尺寸阈值
    geom.rects.append((180.0, 180.0, big, big, False))
    geom.rect_layers.append("M-EQPM")
    geom.rect_blocks.append("SB-1")
    geom.texts.append((60.0, 40.0, "1:100"))
    geom.texts.append((200.0, 200.0, "水泵"))
    result = recognize(geom, "mep", "d9")
    assert result.equipment
    assert result.equipment[0]["label"] == "水泵"


def _square_poly(cx: float, cy: float, side_m: float) -> list[tuple[float, float]]:
    """以 (cx,cy) 为左下角、边长 side_m（米）的闭合方形多边形（页面点坐标）。"""
    s = side_m * PT_PER_M
    return [(cx, cy), (cx + s, cy), (cx + s, cy + s), (cx, cy + s)]


def _plan_with_slab_polys(layers: list[str]) -> DrawingGeometry:
    """基础平面图：为每个 layer 放一块 5m×5m(=25㎡) 闭合板多边形。"""
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    for idx, layer in enumerate(layers):
        geom.polys.append(_square_poly(120.0 + idx * 220.0, 150.0, 5.0))
        geom.poly_layers.append(layer)
        geom.poly_blocks.append("")
    geom.texts.append((60.0, 40.0, "1:100"))
    geom.texts.append((360.0, 20.0, "地下室基础平面图"))
    return geom


@pytest.mark.unit
def test_raft_slab_recognized_via_layer_with_thicker_default():
    """底板图层的多边形识别为筏板（kind=raft）且默认厚度远厚于普通楼板。"""
    result = recognize(_plan_with_slab_polys(["底板"]), "structure", "df")
    assert len(result.slabs) == 1
    slab = result.slabs[0]
    assert slab["kind"] == "raft"
    assert slab["thickness"] == pytest.approx(0.5)


@pytest.mark.unit
def test_multiple_slab_polys_all_collected():
    """多块 slab 图层多边形全部产出（修复「每图仅一块板」）。"""
    result = recognize(_plan_with_slab_polys(["S-SLAB", "S-SLAB"]), "structure", "df")
    assert len(result.slabs) == 2
    assert all(s["kind"] == "slab" and s["thickness"] == pytest.approx(0.12) for s in result.slabs)


@pytest.mark.unit
def test_ordinary_slab_layer_not_tagged_raft():
    """普通楼板图层不误判为筏板，厚度取楼板默认值。"""
    result = recognize(_plan_with_slab_polys(["S-SLAB"]), "structure", "df")
    assert result.slabs[0]["kind"] == "slab"


def _plan_thick_parallel_wall(layer: str, gap_m: float) -> DrawingGeometry:
    """两条平行水平线（间距 gap_m、重叠 2m），图层=layer；无轴网干扰。"""
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    x0, x1 = 200.0, 200.0 + 2.0 * PT_PER_M  # 重叠 2m ≥ _PAIR_MIN_OVERLAP_M
    y = 300.0
    for yy in (y, y + gap_m * PT_PER_M):
        geom.lines.append((x0, yy, x1, yy))
        geom.line_layers.append(layer)
    geom.texts.append((60.0, 40.0, "1:100"))
    geom.texts.append((360.0, 20.0, "地下室结构平面图"))
    return geom


@pytest.mark.unit
def test_basement_exterior_wall_recognized_via_layer_wide_gap():
    """地下室外墙(0.6m 厚)超普通间距上限，靠墙图层放宽间距被召回。"""
    result = recognize(_plan_thick_parallel_wall("地下室外墙", 0.6), "structure", "dw")
    assert len(result.walls) == 1
    assert result.walls[0]["width"] == pytest.approx(0.6, abs=0.02)


@pytest.mark.unit
def test_thick_parallel_lines_dropped_without_wall_layer():
    """同样 0.6m 间距但无墙图层 → 按普通上限丢弃（零回归对照）。"""
    result = recognize(_plan_thick_parallel_wall("", 0.6), "structure", "dw")
    assert result.walls == []


@pytest.mark.unit
def test_pipe_system_from_layer():
    """机电图：管线系统由图层判定（消防）优先于全图关键词。"""
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    for x in (120.0, 360.0):
        geom.lines.append((x, 30.0, x, PAGE_H - 30.0))
        geom.line_layers.append("AXIS")
    for y in (120.0, 360.0):
        geom.lines.append((30.0, y, PAGE_W - 30.0, y))
        geom.line_layers.append("AXIS")
    pipe_len = 5.0 * PT_PER_M
    geom.lines.append((150.0, 240.0, 150.0 + pipe_len, 240.0))
    geom.line_layers.append("消防")
    geom.texts.append((60.0, 40.0, "1:100"))
    result = recognize(geom, "mep", "d9")
    assert any(p["system"] == "消防" for p in result.pipes)


# --- 图层名是「是什么类型」的证据，不是「是不是真构件」的证据 -----------

def _plan_with_hatch_on_column_layer(side_m: float) -> DrawingGeometry:
    """柱图层上混入 side_m 见方的碎多边形（剖面填充线 / 钢筋的常见表达）。"""
    geom = _plan_unfilled_columns("S-COLU")
    s = side_m * PT_PER_M
    for k in range(6):
        x = 150.0 + k * 20.0
        geom.polys.append([(x, 200.0), (x + s, 200.0),
                               (x + s, 200.0 + s), (x, 200.0 + s)])
        geom.poly_layers.append("S-COLU")
        geom.poly_blocks.append("")
    return geom


@pytest.mark.unit
def test_millimetre_polys_on_column_layer_are_not_columns():
    """1 毫米的多边形落在柱图层上也不是柱。

    实测柱框边长**最小 0.001m**、10 分位仅 0.05m —— 多边形分支上
    图层一判为柱就全盘接收、不设尺寸下限，填充线与钢筋因而变成「柱」。
    """
    result = recognize(_plan_with_hatch_on_column_layer(0.001), "structure", "d1")
    assert len(result.columns) == 4          # 只剩 4 根真柱，6 个碎片被拒


@pytest.mark.unit
def test_undersized_but_plausible_poly_on_column_layer_still_kept():
    """0.25m 的小柱仍收 —— 底线是排除物理荒谬，不是收紧到典型值。"""
    result = recognize(_plan_with_hatch_on_column_layer(0.25), "structure", "d1")
    assert len(result.columns) == 10         # 4 根柱 + 6 个合理尺寸多边形


@pytest.mark.unit
def test_oversized_poly_on_column_layer_is_not_a_column():
    """7 米见方的东西落在柱图层上也不是柱。

    实测柱图层上有 64 个（大歌剧院）/ 17 个（轨道交通）超过 3m 的「柱」，
    最大 **7.0m**。它们本身是误检，更要命的是**去重时会把内部的真柱
    一口吞掉**——实测最严重的一个输出框吞掉了 33 个输入框。
    """
    result = recognize(_plan_with_hatch_on_column_layer(7.0), "structure", "d1")
    assert len(result.columns) == 4


# --- 存进模型的轮廓必须保住构件的真实范围 -----------------------------

def _plan_with_many_point_columns() -> DrawingGeometry:
    """柱画成 40 点的近似圆（真实图纸里 51% 的多边形超过 8 个点）。"""
    import math

    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    for x in (120.0, 360.0):
        geom.lines.append((x, 30.0, x, PAGE_H - 30.0))
        geom.line_layers.append("AXIS")
    for y in (120.0, 360.0):
        geom.lines.append((30.0, y, PAGE_W - 30.0, y))
        geom.line_layers.append("AXIS")
    r = 0.3 * PT_PER_M                       # 直径 0.6m 的圆柱
    ring = [(200.0 + r * math.cos(2 * math.pi * k / 40),
             200.0 + r * math.sin(2 * math.pi * k / 40)) for k in range(40)]
    geom.polys.append(ring)
    geom.poly_layers.append("S-COLU")
    geom.poly_blocks.append("")
    geom.texts.append((60.0, 40.0, "1:100"))
    geom.texts.append((400.0, 20.0, "一层柱结构平面图"))
    return geom


@pytest.mark.unit
def test_many_point_column_keeps_its_full_extent():
    """多点多边形存进模型时不得被砍成碎条。

    实测缺陷：尺寸检查用的是**完整多边形**的包围盒（通过），
    存进 outline 的却是 `poly[:8]`——**前 8 个点**。某图 728 根柱里
    124 根因此变成 0.718×0.068m 这样的碎条，而它们的 outline
    点数**恰好都是 8**。3D 渲染与算量吃的都是这些碎条。
    """
    result = recognize(_plan_with_many_point_columns(), "structure", "d1")
    assert len(result.columns) == 1
    outline = result.columns[0]["outline"]
    width = max(p[0] for p in outline) - min(p[0] for p in outline)
    height = max(p[1] for p in outline) - min(p[1] for p in outline)
    assert width == pytest.approx(0.6, abs=0.05)
    assert height == pytest.approx(0.6, abs=0.05)


# --- 标注图层不产出构件：这道闸此前只挡住了一半路径 -------------------

@pytest.mark.unit
def test_bare_text_layer_is_recognised_as_annotation():
    """裸图层名 `TEXT` 也是标注层。

    实测「屋顶设备层埋件平面布置图」：`TEXT` 图层上有 21 个多边形
    落在柱的尺寸区间——那是文字的轮廓。旧正则写作
    `-(?:TEXT|...)`，**要求前导横线**，于是裸 `TEXT` 漏网。
    """
    from core.model3d.layer_conventions import is_annotation_layer

    assert is_annotation_layer("TEXT")
    assert is_annotation_layer("DIM")
    assert is_annotation_layer("S-COLU-TEXT")
    assert not is_annotation_layer("TEXTURE")     # 不能误伤


def _plan_with_polys_on(layer: str, side_m: float = 0.6,
                        count: int = 5) -> DrawingGeometry:
    geom = _plan_unfilled_columns("S-COLU")
    s = side_m * PT_PER_M
    for k in range(count):
        x = 150.0 + k * 60.0
        geom.polys.append([(x, 250.0), (x + s, 250.0),
                           (x + s, 250.0 + s), (x, 250.0 + s)])
        geom.poly_layers.append(layer)
        geom.poly_blocks.append("")
    return geom


@pytest.mark.unit
def test_annotation_layer_blocked_on_the_size_guess_path_too():
    """标注层上**尺寸正好像柱**的多边形也不算柱。

    这是本文件里写了三遍的纪律「标注图层不产出构件」，但实现只把它
    接在 `is_column_layer` 上 —— 标注层的多边形照样能从
    `_is_column_size` 这条**猜测路径**混进来。注释说的是一回事，
    代码做的是另一回事。
    """
    result = recognize(_plan_with_polys_on("TEXT"), "structure", "d1")
    assert len(result.columns) == 4          # 只剩 4 根真柱


@pytest.mark.unit
def test_ordinary_layer_still_reaches_the_size_guess_path():
    """非标注层不受影响——零回归对照。"""
    result = recognize(_plan_with_polys_on("0"), "structure", "d1")
    assert len(result.columns) == 9          # 4 根真柱 + 5 个尺寸像柱的


@pytest.mark.unit
def test_embedded_part_layout_is_not_a_column_source():
    """埋件布置图上不按尺寸猜柱——方形预埋件正好落在柱的尺寸区间。

    实测「屋顶设备层埋件平面布置图」造出 93 根假柱。图上没有任何
    图层被判为柱，552 个多边形**全靠尺寸猜**。

    只收「埋件」一个词：实测「设备」命中的是**楼层名**（四层夹层
    （设备层）隔声隔振）、「标高」命中的是「—8.200 标高地下二层夹层
    **结构**平面图」、「布置图」命中的是「地下一层**换撑**平面布置图」
    ——都是正经结构图，排除它们等于误杀。
    """
    geom = _plan_with_polys_on("0")
    geom.texts.append((400.0, 60.0, "屋顶设备层埋件平面布置图"))
    result = recognize(geom, "structure", "d1",
                       drawing_title="屋顶设备层埋件平面布置图")
    assert len(result.columns) == 4          # 图层明确的 4 根柱仍在，猜的 5 个没了


@pytest.mark.unit
def test_a_layer_that_says_door_never_becomes_a_column():
    """图层已明说是门，就不能再按尺寸猜成柱。

    **实测**（轨道交通装修/景观图）：这些「柱」落在
    `A—门窗`(door) / `I—平面—门`(door) / `A-GLAZ`(window) /
    `A—设备管丼、主管符号`(pipe) / `景-平面-红线`(slab) 上 ——
    分类器**答得出**它们是什么，识别器却仍从尺寸猜测路径把它们收成柱。

    金标准上这两块（G19 金标准 0、G23 金标准 2）合计贡献了修复后
    残余误差的 **69%**：框住的是文字字形、标高符号的黑三角、门扇符号。
    """
    result = recognize(_plan_with_polys_on("A—门窗"), "structure", "d1")
    assert len(result.columns) == 4


@pytest.mark.unit
def test_an_unclassifiable_layer_still_reaches_the_size_guess():
    """判不出类型的图层照旧走尺寸猜测——不因这道闸而漏检。"""
    result = recognize(_plan_with_polys_on("XYZ-999"), "structure", "d1")
    assert len(result.columns) == 9
