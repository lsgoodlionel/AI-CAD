"""从档案碎片重建成篇说明。

**需求**：「各专业图纸的说明输出到图纸信息模块，**完整**把文字全部识别
出来」「每张图纸的文字说明**单独**识别储存」——后期建模和审图中
这是所有内容的总要求和验证起点。

**现状实测**：档案里 249303 条 `note` 平均 **14 字符**、`other` 平均
**7 字符** —— 存的是 OCR 的**行级碎片**，不是成篇说明。
样本里能直接看到跨行截断：「位应结合试成桩试验的结果提供详尽的…」
开头缺字（原句是「监理单位应结合…」）。

好在碎片**带 bbox 且置信 0.92~0.99**，所以整篇说明可以从档案重建，
不必重跑 OCR（符合 Phase E「抽取一次·单一真相源」）。
"""
import pytest


def _tok(text, x, y, w=60.0, h=8.0):
    return {"content": text, "location_json": {"bbox": [x, y, x + w, y + h]}}


# ── 碎片 → token ──────────────────────────────────────────────

@pytest.mark.unit
def test_archive_rows_become_positioned_tokens():
    from services.drawing_spec_text import tokens_from_archive

    tokens = tokens_from_archive([_tok("一、总则", 100, 200)])
    assert tokens[0]["text"] == "一、总则"
    assert tokens[0]["x"] == 100 and tokens[0]["y"] == 200


@pytest.mark.unit
def test_rows_without_position_are_dropped():
    """没位置的碎片无法参与排序——**留着会把顺序搅乱**，
    宁可丢掉并让丢弃量可见。"""
    from services.drawing_spec_text import tokens_from_archive

    tokens = tokens_from_archive([
        {"content": "有位置", "location_json": {"bbox": [1, 2, 3, 4]}},
        {"content": "没位置", "location_json": None},
        {"content": "空 bbox", "location_json": {}},
    ])
    assert [t["text"] for t in tokens] == ["有位置"]


# ── 说明块识别 ────────────────────────────────────────────────

#: **实测的真实标题**（按出现次数）：`说明：`385、`设计说明`81、
#: `说明`52、`技术说明：`33、`修改说明：`8、`设计说明1`8。
#: 固定前缀表覆盖不了，判据改为「短前缀 + 说明」。
@pytest.mark.unit
@pytest.mark.parametrize("heading", [
    "说明：", "设计说明", "说明", "技术说明：", "修改说明：", "设计说明1",
    "结构设计总说明", "施工说明", "注：", "一、总则", "十二，土方开挖：",
    "技术要求", "施工技术要求",
])
def test_note_headings_recognised(heading):
    from services.drawing_spec_text import is_note_heading

    assert is_note_heading(heading)


#: 这些**都是真实档案里含「说明」的文本**——正文里的交叉引用最容易
#: 被误判成标题，而误判一次就会凭空多出一个空说明块。
@pytest.mark.unit
@pytest.mark.parametrize("other", [
    "5.400", "KZ1", "1-A", "客厅", "DN100", "C30", "",
    "无障碍出入口，做法详设计说明",
    "2.抗震等级见总说明。",
    "9.其余未尽事宜详见总说明。",
    "根据设计说明及规范，大于等于",
    "同时满足产品说明",
    "8.卫生间、淋浴间给排水管材详见给排水设计说明。",
    "说明书编号 A-101",
])
def test_non_headings_rejected(other):
    from services.drawing_spec_text import is_note_heading

    assert not is_note_heading(other)


# ── 成篇重建 ──────────────────────────────────────────────────

@pytest.mark.unit
def test_lines_in_one_column_are_joined_in_reading_order():
    """同一列内按 y 递增拼接——这是把碎片还原成句子的前提。"""
    from services.drawing_spec_text import (assemble_spec_blocks,
                                            tokens_from_archive)

    blocks = assemble_spec_blocks(tokens_from_archive([
        _tok("三、施工要求", 100, 100),
        _tok("1. 基坑开挖应分层进行，", 100, 112),
        _tok("每层不大于2m。", 100, 124),
    ]))
    assert len(blocks) == 1
    assert blocks[0]["title"] == "三、施工要求"
    assert "分层进行" in blocks[0]["text"]
    assert blocks[0]["text"].index("基坑开挖") < blocks[0]["text"].index("每层不大于")


@pytest.mark.unit
def test_separate_columns_become_separate_blocks():
    """图纸说明常排成多栏。**按栏切开**，否则左右栏会交替串成乱码。"""
    from services.drawing_spec_text import (assemble_spec_blocks,
                                            tokens_from_archive)

    blocks = assemble_spec_blocks(tokens_from_archive([
        _tok("一、设计依据", 100, 100),
        _tok("依据甲方要求。", 100, 112),
        _tok("二、材料要求", 900, 100),
        _tok("混凝土强度等级C30。", 900, 112),
    ]))
    titles = sorted(b["title"] for b in blocks)
    assert titles == ["一、设计依据", "二、材料要求"]
    joined = " ".join(b["text"] for b in blocks)
    assert "依据甲方要求" in joined and "混凝土强度等级C30" in joined
    for block in blocks:
        assert not ("甲方" in block["text"] and "C30" in block["text"]), "两栏串了"


@pytest.mark.unit
def test_blocks_without_heading_are_not_returned():
    """没有说明标题的文字块不算说明——图上的房间名、尺寸、轴号
    都会落进来，混进去会把「说明」这个概念稀释掉。"""
    from services.drawing_spec_text import (assemble_spec_blocks,
                                            tokens_from_archive)

    blocks = assemble_spec_blocks(tokens_from_archive([
        _tok("客厅", 100, 100),
        _tok("5.400", 100, 112),
        _tok("KZ1", 100, 124),
    ]))
    assert blocks == []


@pytest.mark.unit
def test_block_records_source_fragment_count():
    """记下这块由多少条碎片拼成——人工复核时要能回溯，
    也是「重建是否吃掉了内容」的检查手段。"""
    from services.drawing_spec_text import (assemble_spec_blocks,
                                            tokens_from_archive)

    blocks = assemble_spec_blocks(tokens_from_archive([
        _tok("说明：", 100, 100),
        _tok("1. 未注明尺寸均以标注为准。", 100, 112),
        _tok("2. 材料代换须经设计确认。", 100, 124),
    ]))
    assert blocks[0]["fragment_count"] == 3


@pytest.mark.unit
def test_location_json_may_arrive_as_a_string():
    """**实测**：`location_json` 从库里回来是 JSON **字符串**，不是 dict
    （驱动对 jsonb 的处理）。单测喂 dict 时抓不到，
    真库上直接 `'str' object has no attribute 'get'`。
    """
    from services.drawing_spec_text import tokens_from_archive

    tokens = tokens_from_archive([
        {"content": "一、总则", "location_json": '{"bbox": [10, 20, 70, 28]}'},
    ])
    assert tokens[0]["x"] == 10 and tokens[0]["y"] == 20


@pytest.mark.unit
def test_malformed_location_json_is_dropped_not_raised():
    """坏 JSON 只丢这一条，不能让整张图的重建炸掉。"""
    from services.drawing_spec_text import tokens_from_archive

    tokens = tokens_from_archive([
        {"content": "坏的", "location_json": "{不是 json"},
        {"content": "好的", "location_json": '{"bbox": [1, 2, 3, 4]}'},
    ])
    assert [t["text"] for t in tokens] == ["好的"]


@pytest.mark.unit
def test_both_position_shapes_are_accepted():
    """**档案层有两种位置结构**（`reading_order._xy_of` 的注释早写了）：
    OCR 存 `{"bbox": […]}`、矢量文字存 `{"x":…, "y":…}`。

    实测只认 bbox 会丢掉 **317045 条**矢量文字碎片（占三类总量的 33%），
    而重建说明恰恰最需要矢量文字——它没有 OCR 误差。
    """
    from services.drawing_spec_text import tokens_from_archive

    tokens = tokens_from_archive([
        {"content": "OCR 的", "location_json": '{"bbox": [10, 20, 70, 28]}'},
        {"content": "矢量的", "location_json": '{"x": 30.5, "y": 40.5}'},
        {"content": "矢量 dict", "location_json": {"x": 50, "y": 60}},
    ])
    assert [t["text"] for t in tokens] == ["OCR 的", "矢量的", "矢量 dict"]
    assert tokens[1]["x"] == 30.5 and tokens[1]["y"] == 40.5


@pytest.mark.unit
def test_heading_in_the_middle_of_a_column_still_starts_a_block():
    """**实测**：真实图纸一栏里有上千碎片，说明标题在栏中间而非栏首。
    要求「栏首即标题」时三张样图全部检出 0 块。"""
    from services.drawing_spec_text import (assemble_spec_blocks,
                                            tokens_from_archive)

    blocks = assemble_spec_blocks(tokens_from_archive([
        _tok("平面图", 100, 50),
        _tok("客厅", 100, 62),
        _tok("说明：", 100, 100),
        _tok("1. 未注明尺寸以标注为准。", 100, 112),
    ]))
    assert len(blocks) == 1
    assert blocks[0]["title"] == "说明："
    assert "未注明尺寸" in blocks[0]["text"]
    assert "客厅" not in blocks[0]["text"], "标题以上的内容不属于这块说明"


@pytest.mark.unit
def test_next_heading_ends_the_previous_block():
    """一栏里可能有多段说明，后一个标题就是前一块的边界。"""
    from services.drawing_spec_text import (assemble_spec_blocks,
                                            tokens_from_archive)

    blocks = assemble_spec_blocks(tokens_from_archive([
        _tok("设计说明", 100, 100),
        _tok("依据甲方设计任务书及现行国家规范。", 100, 112),
        _tok("施工说明", 100, 200),
        _tok("基坑开挖应分层进行，每层不大于2m。", 100, 212),
    ]))
    assert [b["title"] for b in blocks] == ["设计说明", "施工说明"]
    assert "基坑开挖" not in blocks[0]["text"]
    assert "甲方" not in blocks[1]["text"]


@pytest.mark.unit
def test_large_vertical_gap_ends_a_block():
    """说明块下方远处的零散文字不属于它——图纸上一栏能横跨整张图，
    不设距离上限会把半张图的文字都吞进来。"""
    from services.drawing_spec_text import (assemble_spec_blocks,
                                            tokens_from_archive)

    blocks = assemble_spec_blocks(tokens_from_archive([
        _tok("说明：", 100, 100),
        _tok("1. 第一条。", 100, 112),
        _tok("图例", 100, 900),          # 远在下方，与说明无关
    ]))
    assert "图例" not in blocks[0]["text"]


@pytest.mark.unit
def test_heading_with_no_body_is_dropped():
    """只有标题没有正文的不算说明块——多半是误判的标题。"""
    from services.drawing_spec_text import (assemble_spec_blocks,
                                            tokens_from_archive)

    assert assemble_spec_blocks(tokens_from_archive([_tok("说明：", 100, 100)])) == []


@pytest.mark.unit
def test_title_block_fragments_are_excluded():
    """**实测**：图名叫「设计说明7」的图，重建出的「正文」是标题栏字段
    （DRAWING TITLE / 阶段 / 工程编号 / 图号 / A-00-07A…）。

    图名「设计说明」与说明块标题「设计说明」文本上无法区分，
    但**标题栏的碎片本就不该进说明**——档案里它们有自己的分类。
    """
    from services.drawing_spec_text import tokens_from_archive

    tokens = tokens_from_archive([
        {"content": "说明：", "category": "note",
         "location_json": {"bbox": [10, 20, 70, 28]}},
        {"content": "工程编号", "category": "title_block_label",
         "location_json": {"bbox": [10, 32, 70, 40]}},
        {"content": "A-201555010", "category": "title_block",
         "location_json": {"bbox": [10, 44, 70, 52]}},
    ])
    assert [t["text"] for t in tokens] == ["说明："]


@pytest.mark.unit
def test_rows_without_category_are_kept():
    """没有 category 字段的调用方（比如单测、旧数据）不受影响。"""
    from services.drawing_spec_text import tokens_from_archive

    tokens = tokens_from_archive([
        {"content": "说明：", "location_json": {"bbox": [1, 2, 3, 4]}}])
    assert len(tokens) == 1


@pytest.mark.unit
@pytest.mark.parametrize("sentence", [
    "八，不能上翻。",          # 实测误判：句子片段撞上中文序号模式
    "一，则应按此执行。",
    "三、并应符合规范！",
])
def test_numbered_sentence_fragments_are_not_headings(sentence):
    """中文序号开头**不等于**章节标题——正文换行后的片段常以
    「八，」开头。带句末标点的一律不是标题。"""
    from services.drawing_spec_text import is_note_heading

    assert not is_note_heading(sentence)


@pytest.mark.unit
@pytest.mark.parametrize("heading", [
    "一、总则", "十二，土方开挖：", "十三、深坑加固：", "五、水管道：",
])
def test_real_section_headings_still_pass(heading):
    from services.drawing_spec_text import is_note_heading

    assert is_note_heading(heading)


@pytest.mark.unit
def test_label_grid_blocks_are_rejected_by_line_length():
    """**实测**：图名叫「设计说明N」的图会拼出 3.6 字/行 × 29 行 的块——
    那是标题栏的字段格，不是说明。真说明最短也有约 9 字/行
    （384 块实测分布 P10=5.6、P20=9.4，噪声全在最底部）。

    分类字段不可靠（同一张图的标题栏碎片散落在 room_name /
    title_block_label / other 三个分类），所以改用可测的行长统计。
    """
    from services.drawing_spec_text import (assemble_spec_blocks,
                                            tokens_from_archive)

    rows = [{"content": "设计说明", "location_json": {"bbox": [10, 10, 60, 18]}}]
    rows += [{"content": "图号", "location_json": {"bbox": [10, 20 + i * 10, 40, 28 + i * 10]}}
             for i in range(10)]
    assert assemble_spec_blocks(tokens_from_archive(rows)) == []


@pytest.mark.unit
def test_short_but_real_note_is_kept():
    """短说明不能被误杀——「说明：弱电间铺设600*600静电地板，离地200mm」
    只有一两行，但它是真说明。"""
    from services.drawing_spec_text import (assemble_spec_blocks,
                                            tokens_from_archive)

    blocks = assemble_spec_blocks(tokens_from_archive([
        {"content": "说明：", "location_json": {"bbox": [10, 10, 60, 18]}},
        {"content": "弱电间铺设600*600静电地板，离地200mm",
         "location_json": {"bbox": [10, 22, 200, 30]}},
    ]))
    assert len(blocks) == 1


@pytest.mark.unit
def test_block_carries_avg_line_length_for_review():
    """把行长统计记在块上——人审要能看到「这块为什么可疑」，
    而不是让它被静默丢掉。"""
    from services.drawing_spec_text import (assemble_spec_blocks,
                                            tokens_from_archive)

    blocks = assemble_spec_blocks(tokens_from_archive([
        {"content": "说明：", "location_json": {"bbox": [10, 10, 60, 18]}},
        {"content": "一二三四五六七八九十", "location_json": {"bbox": [10, 22, 90, 30]}},
    ]))
    assert blocks[0]["avg_line_chars"] == 10.0


# ── 落库 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_persist_replaces_only_spec_text_category():
    """**只能删 `spec_text` 这一类**。档案层通用的
    `persist_drawing_info` 是整图覆盖式（先 DELETE 全部再插），
    用它会把这张图 1600 条碎片全删掉——而说明正是从碎片重建来的。
    """
    from services.drawing_spec_text import persist_spec_text

    executed = []

    class DB:
        async def execute(self, sql, params=None):
            executed.append((sql, params))

    await persist_spec_text(DB(), project_id="p1", drawing_id="d1", blocks=[
        {"title": "说明：", "text": "1. 未注明尺寸以标注为准。",
         "fragment_count": 2, "avg_line_chars": 13.0, "x": 10.0, "y": 20.0},
    ])
    deletes = [sql for sql, _ in executed if "DELETE" in sql.upper()]
    assert len(deletes) == 1
    assert "spec_text" in deletes[0] or "category" in deletes[0]
    inserts = [p for sql, p in executed if "INSERT" in sql.upper()]
    assert len(inserts) == 1
    assert inserts[0]["category"] == "spec_text"
    assert inserts[0]["content"] == "1. 未注明尺寸以标注为准。"


@pytest.mark.asyncio
async def test_persist_keeps_title_and_quality_metric_in_value_json():
    """标题与行长统计随块保存——人审面板要按它分流，
    建模/审图消费时也要知道这块说明的来源与可信度。"""
    import json

    from services.drawing_spec_text import persist_spec_text

    captured = []

    class DB:
        async def execute(self, sql, params=None):
            if "INSERT" in sql.upper():
                captured.append(params)

    await persist_spec_text(DB(), project_id="p1", drawing_id="d1", blocks=[
        {"title": "三、施工要求", "text": "正文", "fragment_count": 7,
         "avg_line_chars": 25.0, "x": 1.0, "y": 2.0},
    ])
    value = json.loads(captured[0]["value_json"])
    assert value["title"] == "三、施工要求"
    assert value["fragment_count"] == 7
    assert value["avg_line_chars"] == 25.0


@pytest.mark.asyncio
async def test_empty_blocks_still_clears_stale_spec_text():
    """这轮没重建出说明时也要清掉上一轮的——否则判据改进后，
    被新判据否掉的旧说明会**永远留在库里**（E1.5 的 supersedes 教训）。
    """
    from services.drawing_spec_text import persist_spec_text

    executed = []

    class DB:
        async def execute(self, sql, params=None):
            executed.append(sql)

    await persist_spec_text(DB(), project_id="p1", drawing_id="d1", blocks=[])
    assert any("DELETE" in sql.upper() for sql in executed)


@pytest.mark.unit
def test_task_is_registered_in_celery_include():
    """**注册坑**：不在 `celery_app` 的 `include` 里，任务不会被 worker
    加载，调用时报 `Received unregistered task`——而调用方看不出区别。"""
    from core.celery_app import celery_app

    assert "tasks.drawing_spec_text" in celery_app.conf.include
