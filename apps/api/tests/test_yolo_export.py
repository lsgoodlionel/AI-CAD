"""既有识别结果 → YOLO 训练集。

**数据来源的选择**：图层弱标签（Phase C 的 `auto_label`）实测命中率
只有 6.6%（§8.6），而确定性识别器的输出是现成的 **340 张图 / 76858 个框**，
带类别带轮廓。

**但必须先验标注质量**：这些标注来自识别器，而识别器有已知错误模式
（本轮修过钢筋图层的 3410 个假柱、516 面本该是梁的墙）。
在错标注上训练等于教模型复制错误——所以导出之后、训练之前要人工抽检。
"""
import pytest


@pytest.mark.unit
def test_outline_becomes_a_normalised_box():
    """YOLO 要的是归一化的中心点 + 宽高。"""
    from core.model3d.yolo_export import outline_to_yolo_box

    box = outline_to_yolo_box([[10.0, 20.0], [30.0, 60.0]], page_w=100.0, page_h=200.0)
    assert box == pytest.approx((0.2, 0.2, 0.2, 0.2))


@pytest.mark.unit
def test_degenerate_outline_is_dropped():
    """零面积的框训练时是噪声——**宁可少一个样本，不要一个假样本**。"""
    from core.model3d.yolo_export import outline_to_yolo_box

    assert outline_to_yolo_box([[10.0, 20.0]], 100.0, 200.0) is None
    assert outline_to_yolo_box([[10.0, 20.0], [10.0, 20.0]], 100.0, 200.0) is None


@pytest.mark.unit
def test_box_outside_the_page_is_dropped():
    """超出页面的框多半是坐标算错了（本轮实测有单图跨 4176 米的）——
    喂给训练只会教模型学错。"""
    from core.model3d.yolo_export import outline_to_yolo_box

    assert outline_to_yolo_box([[-50.0, 10.0], [-10.0, 20.0]], 100.0, 200.0) is None
    assert outline_to_yolo_box([[10.0, 10.0], [500.0, 20.0]], 100.0, 200.0) is None


@pytest.mark.unit
def test_class_ids_are_stable():
    """**类别 id 必须稳定**：训练好的权重按 id 索引类别，
    顺序一变，模型输出的「柱」就成了「墙」。"""
    from core.model3d.yolo_export import CLASS_NAMES, class_id

    assert CLASS_NAMES[class_id("columns")] == "column"
    assert CLASS_NAMES[class_id("walls")] == "wall"
    assert class_id("不存在的类") is None
    # 与 Phase C 的 9 类体系一致，不另起一套
    assert CLASS_NAMES[:6] == ["column", "wall", "beam", "slab", "pipe", "equipment"]


@pytest.mark.unit
def test_label_lines_are_yolo_format():
    from core.model3d.yolo_export import label_lines

    lines = label_lines([
        {"kind": "columns", "outline": [[10.0, 20.0], [30.0, 60.0]]},
        {"kind": "walls", "path": [[0.0, 0.0], [50.0, 100.0]]},
        {"kind": "未知", "outline": [[1.0, 1.0], [2.0, 2.0]]},
    ], page_w=100.0, page_h=200.0)
    assert len(lines) == 2, "未知类别要丢掉，不能编一个 id"
    assert lines[0].startswith("0 ")
    assert all(len(l.split()) == 5 for l in lines)


@pytest.mark.unit
def test_recognizer_exposes_its_own_transform():
    """**构件坐标不走 `drawing_transform`**——识别器用它自己算的
    scale 和 origin（`_Ctx`）。这条本轮早先就记录过：
    「修好 S-0-20-102.04C 的 drawing_transform 后构件坐标纹丝不动」。

    所以导出训练集时**不能拿那张表反算页面坐标**——实测框全部错位：
    真正的柱子一个没框上，几个红框挤在图幅左边缘。
    识别器必须把自己用的那组参数暴露出来。
    """
    from core.model3d.element_recognizer import FloorElements

    fe = FloorElements()
    # 比例字段本来就叫 scale（不是 scale_m_pt）——断言要对着真实字段
    for field in ("scale", "origin_pt", "page_h"):
        assert hasattr(fe, field), f"FloorElements 缺 {field}，导出无法反算页面坐标"


@pytest.mark.unit
def test_meters_convert_back_to_page_points():
    """逆变换必须与 `_Ctx.to_m` 严格互逆。"""
    from core.model3d.yolo_export import meters_to_page

    # to_m: fx = x - ox; fy = (page_h - y) - oy; 乘 scale
    scale, ox, oy, ph = 0.05, 100.0, 50.0, 800.0
    x_pt, y_pt = 300.0, 200.0
    x_m = (x_pt - ox) * scale
    y_m = ((ph - y_pt) - oy) * scale
    back = meters_to_page(x_m, y_m, scale, (ox, oy), ph)
    assert back[0] == pytest.approx(x_pt)
    assert back[1] == pytest.approx(y_pt)


# ── 切片：大幅面图纸的必要步骤 ─────────────────────────────────

@pytest.mark.unit
def test_tiling_is_required_by_measurement():
    """**实测**：整图训练时框的中位尺寸在 1024px 下只有 **3.7 × 4.8 像素**，
    P10 是 1.7 × 0.9 像素。YOLO 最小可检测目标约 8~10 像素，
    而这些框在 P3 特征层（1/8 下采样）上只剩 0.46 像素——**根本训不出来**。

    切片把整图切成 640px 小块，构件在块内的相对尺寸放大约 7 倍。
    这是大幅面图纸/遥感检测的标准做法。
    """
    from core.model3d.yolo_export import tile_grid

    tiles = tile_grid(4681, 3312, tile=640, overlap=64)
    assert len(tiles) > 40, "4681×3312 应切出几十块"
    # 重叠是必须的：构件跨在切线上时两块各留一部分，不重叠就两边都不完整
    x0s = sorted({t[0] for t in tiles})
    assert x0s[1] - x0s[0] == 640 - 64


@pytest.mark.unit
def test_boxes_are_clipped_and_reindexed_per_tile():
    """框要按块重新归一化；跨块的框裁到块内。"""
    from core.model3d.yolo_export import boxes_in_tile

    # 页面 1000×1000，块 (0,0,500,500)，框中心 (250,250) 宽高 100
    got = boxes_in_tile([(0, 0.25, 0.25, 0.1, 0.1)], 1000, 1000,
                        (0, 0, 500, 500))
    assert len(got) == 1
    cls, cx, cy, w, h = got[0]
    assert cls == 0
    assert cx == pytest.approx(0.5) and cy == pytest.approx(0.5)
    assert w == pytest.approx(0.2)     # 100/500


@pytest.mark.unit
def test_boxes_outside_the_tile_are_dropped():
    from core.model3d.yolo_export import boxes_in_tile

    assert boxes_in_tile([(0, 0.9, 0.9, 0.02, 0.02)], 1000, 1000,
                         (0, 0, 500, 500)) == []


@pytest.mark.unit
def test_tiny_slivers_at_the_edge_are_dropped():
    """跨在切线上只剩一丝的框是噪声——**宁可少一个样本，不要一个残框**。"""
    from core.model3d.yolo_export import boxes_in_tile

    # 框中心在块外，只有极小一部分伸进来
    got = boxes_in_tile([(0, 0.505, 0.5, 0.02, 0.02)], 1000, 1000,
                        (0, 0, 500, 500))
    assert got == []
