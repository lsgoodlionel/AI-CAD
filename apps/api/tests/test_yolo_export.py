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


@pytest.mark.unit
def test_duplicate_boxes_are_merged():
    """**规则引擎对同一根柱吐多个重叠框。**

    实测（三方独立核对）：N8 切片 24 个原始框，合并重叠后 **6 个**，
    而 GPT 数 6、我自己数也是 6——三方吻合。
    6 块抽样里有 3 块去重后与 GPT 计数完全对上（比值 0.88~1.10）。

    重复框对训练是有害的：它教模型输出重复，并让那些区域的损失被重复计算。
    """
    from core.model3d.yolo_export import merge_duplicate_boxes

    boxes = [
        (0, 0.10, 0.10, 0.04, 0.04),
        (0, 0.105, 0.102, 0.042, 0.04),   # 与上一个高度重叠
        (0, 0.50, 0.50, 0.04, 0.04),      # 独立
    ]
    out = merge_duplicate_boxes(boxes)
    assert len(out) == 2


@pytest.mark.unit
def test_merged_box_covers_the_union():
    """合并后的框取并集——**不能只留其中一个**，
    那会丢掉柱的真实轮廓范围。"""
    from core.model3d.yolo_export import merge_duplicate_boxes

    out = merge_duplicate_boxes([
        (0, 0.10, 0.10, 0.04, 0.04),
        (0, 0.12, 0.10, 0.04, 0.04),
    ])
    assert len(out) == 1
    _cls, cx, cy, w, h = out[0]
    assert w > 0.04, "并集应比单个框宽"


@pytest.mark.unit
def test_different_classes_are_never_merged():
    """柱与板重叠是常态（柱站在板上）——不同类别不能合并。"""
    from core.model3d.yolo_export import merge_duplicate_boxes

    out = merge_duplicate_boxes([
        (0, 0.10, 0.10, 0.04, 0.04),
        (3, 0.10, 0.10, 0.04, 0.04),
    ])
    assert len(out) == 2


# --- 包含式重复：IoU 去重的盲区 ---------------------------------------

def test_merge_absorbs_small_box_contained_in_large_one():
    """小框套在大框里时 IoU 很低，却是同一根柱的重复输出。

    实测：柱框中 14~20% 被更大的框实质包含，最严重一图 95%（381/400）。
    IoU=小/大，套在大框里的小框 IoU 可低于 0.1 而逃过 IoU 去重。
    """
    from core.model3d.yolo_export import merge_duplicate_boxes, _box_iou

    big = (0, 0.5, 0.5, 0.40, 0.40)
    small = (0, 0.5, 0.5, 0.08, 0.08)          # 完全在 big 内部
    assert _box_iou((0.30, 0.30, 0.70, 0.70),
                                (0.46, 0.46, 0.54, 0.54)) < 0.1
    assert len(merge_duplicate_boxes([big, small])) == 1


def test_merge_keeps_contained_box_of_different_class():
    """柱站在板上是常态——包含关系不跨类别合并。"""
    from core.model3d.yolo_export import merge_duplicate_boxes

    slab = (3, 0.5, 0.5, 0.90, 0.90)
    column = (0, 0.5, 0.5, 0.05, 0.05)
    assert len(merge_duplicate_boxes([slab, column])) == 2


def test_merge_keeps_adjacent_boxes_that_merely_touch():
    """相邻但互不包含的两根柱不能被合并掉。"""
    from core.model3d.yolo_export import merge_duplicate_boxes

    a = (0, 0.30, 0.5, 0.10, 0.10)
    b = (0, 0.41, 0.5, 0.10, 0.10)
    assert len(merge_duplicate_boxes([a, b])) == 2


def test_label_lines_dedups_by_default():
    """去重必须在导出主路径上，而不是靠调用方每次记得做一遍。"""
    from core.model3d.yolo_export import label_lines

    big = {"kind": "columns", "outline": [[10.0, 10.0], [50.0, 50.0]]}
    inside = {"kind": "columns", "outline": [[28.0, 28.0], [32.0, 32.0]]}
    assert len(label_lines([big, inside], page_w=100.0, page_h=100.0)) == 1
    assert len(label_lines([big, inside], page_w=100.0, page_h=100.0,
                           dedupe=False)) == 2
