"""用矢量边框给说明块定边界。

**阶段 A 改向的第三条**：图纸上的说明区域**常有矢量边框**——
实测四张说明字数最多的图，每个说明块都被 2~4 个矢量矩形包住。
这是图纸独有的结构信号：文档版面模型看不见它（§8.36 实测它们
把整张图判成一个 `figure`），而我们能直接读矢量。

现有的块边界靠两条启发式：遇到下一个标题、或垂直间距超过 60pt。
边框比这两条都准——它是**制图者画出来的**真实边界。
"""
import pytest


def _r(x0, y0, x1, y1):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


@pytest.mark.unit
def test_smallest_enclosing_frame_is_chosen():
    """一个点会被多个矩形包住（图框、图签区、说明框）——
    要的是**最小**的那个，否则边界会宽到把半张图圈进来。"""
    from core.model3d.spec_frame import enclosing_frame

    frames = [_r(0, 0, 2000, 1400), _r(100, 200, 900, 800), _r(120, 250, 400, 600)]
    got = enclosing_frame(frames, 200, 300)
    assert (got["x0"], got["y0"], got["x1"], got["y1"]) == (120, 250, 400, 600)


@pytest.mark.unit
def test_point_outside_every_frame_yields_none():
    """框不住就说框不住——不给一个「最近的」凑数。"""
    from core.model3d.spec_frame import enclosing_frame

    assert enclosing_frame([_r(0, 0, 100, 100)], 500, 500) is None
    assert enclosing_frame([], 1, 1) is None
    assert enclosing_frame(None, 1, 1) is None


@pytest.mark.unit
def test_degenerate_frames_are_ignored():
    """零宽/零高的「矩形」其实是线段，不能当边框用。"""
    from core.model3d.spec_frame import enclosing_frame

    assert enclosing_frame([_r(0, 100, 900, 100)], 400, 100) is None


@pytest.mark.unit
def test_tiny_frames_are_ignored():
    """字号大小的小方框（表格单元、符号）不是说明框——
    用它定边界会把一个块碎成几十片。"""
    from core.model3d.spec_frame import enclosing_frame, MIN_FRAME_SIDE_PT

    small = MIN_FRAME_SIDE_PT - 1
    assert enclosing_frame([_r(0, 0, small, small)], 1, 1) is None


# ── 用边框切块 ────────────────────────────────────────────────

@pytest.mark.unit
def test_frame_never_truncates_below_the_gap_heuristic():
    """**边框只能帮忙，不能截断。**

    实测教训：图上有 67 个矢量框，「最小包围矩形」选中的是标题附近的
    小框（表格单元之类）而非说明框，于是把正文切掉了——
    「八，施工安装要求」1390 → **46 字**，切掉的是
    「（2）承插连接的给水管…（3）综合管线…」这样的正文。
    25 张图批量 A/B：字数 **-13%**，全是合法内容的损失。

    改为：在包住标题的所有框里选能容纳**最长连续正文**的那个；
    若仍不如间距启发式，就用间距启发式。
    """
    from services.drawing_spec_text import assemble_spec_blocks, tokens_from_archive

    rows = [
        {"content": "说明：", "location_json": {"x": 130, "y": 260}},
        {"content": "1. 未注明尺寸均以标注为准。", "location_json": {"x": 130, "y": 280}},
        {"content": "2. 材料代换须经设计确认。", "location_json": {"x": 130, "y": 300}},
        # 框外、且间距不到 60pt —— 只有边框能把它挡在外面
        {"content": "图例说明另见附表A", "location_json": {"x": 130, "y": 640}},
    ]
    # 小框只容得下一行 —— 不得据此把块截断到一行
    blocks = assemble_spec_blocks(tokens_from_archive(rows),
                                  frames=[_r(120, 250, 400, 290),
                                          _r(120, 250, 400, 620)])
    assert len(blocks) == 1
    assert "材料代换" in blocks[0]["text"], "被小框截断了"
    assert "图例" not in blocks[0]["text"]


@pytest.mark.unit
def test_frame_extends_a_block_across_a_wide_gap():
    """边框的真正价值：跨过大间距把同一块连起来——
    间距启发式在这里会误断，而边框知道它们同属一框。"""
    from services.drawing_spec_text import assemble_spec_blocks, tokens_from_archive

    rows = [
        {"content": "说明：", "location_json": {"x": 130, "y": 260}},
        {"content": "1. 未注明尺寸均以标注为准。", "location_json": {"x": 130, "y": 280}},
        # 与上一行相隔 200pt（远超 60pt 的启发式阈值），但同在一个框内
        {"content": "2. 材料代换须经设计确认。", "location_json": {"x": 130, "y": 480}},
    ]
    blocks = assemble_spec_blocks(tokens_from_archive(rows),
                                  frames=[_r(120, 250, 400, 620)])
    assert "材料代换" in blocks[0]["text"]


@pytest.mark.unit
def test_without_frames_behaviour_is_unchanged():
    """没有边框数据时退回原有启发式——不能因为新能力就要求人人都有它。"""
    from services.drawing_spec_text import assemble_spec_blocks, tokens_from_archive

    rows = [
        {"content": "说明：", "location_json": {"x": 130, "y": 260}},
        {"content": "1. 未注明尺寸均以标注为准。", "location_json": {"x": 130, "y": 280}},
    ]
    assert len(assemble_spec_blocks(tokens_from_archive(rows))) == 1


@pytest.mark.unit
def test_frames_are_extracted_from_vector_drawings():
    """从 PDF 矢量图形里取矩形——**不渲染**，只读几何，
    所以整项目跑一遍的代价可控。"""
    from services.drawing_spec_text import frames_from_drawings

    class Rect:
        def __init__(self, x0, y0, x1, y1):
            self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
            self.width, self.height = x1 - x0, y1 - y0

    drawings = [
        {"rect": Rect(120, 250, 400, 620)},     # 说明框
        {"rect": Rect(0, 100, 900, 101)},       # 线段（零高）→ 排除
        {"rect": Rect(0, 0, 10, 10)},           # 小方框 → 排除
        {"no_rect": True},
    ]
    frames = frames_from_drawings(drawings)
    assert len(frames) == 1
    assert frames[0]["x0"] == 120 and frames[0]["y1"] == 620
