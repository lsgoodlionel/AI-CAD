"""说明块的人审闭环：改了要生效，重跑不能冲掉。

**需求 3**：「提取的文字信息可以人工查看、复核、调整，
调整后的信息可以**直接参与后期模型和审图**」。

**实测暴露两个缺陷**：

① 重建任务删的是整个 `spec_text` 分类，**连人工修正一起删** ——
   实测 verify 写入后重跑单图，verified 记录消失，只剩 auto。

② `normalized_key` 对 `spec_text` 落到「按 content 去空白」，
   **而人审改的就是 content** —— auto 与 verified 永远配不上对，
   界面上会同时出现「原文」和「修正后」两条。
   而且重建后正文会随判据/OCR 微变，`supersedes_key` 也匹配不上，
   于是每次重跑都多出一条僵尸记录。

说明块的**稳定身份是「标题 + 位置」**，不是正文——正文正是被改的那部分。
"""
import pytest


# ── 归一化 key ────────────────────────────────────────────────

@pytest.mark.unit
def test_spec_text_key_ignores_body_edits():
    """人审改正文后仍与原块同 key —— 否则两条并存。"""
    from services.drawing_archive import normalized_key

    auto = normalized_key("spec_text", "原文有错别字",
                          {"title": "五、灌注桩技术要求：", "x": 812.3, "y": 445.6})
    fixed = normalized_key("spec_text", "人工校正后的完整正文，长得多。",
                           {"title": "五、灌注桩技术要求：", "x": 812.3, "y": 445.6})
    assert auto == fixed


@pytest.mark.unit
def test_spec_text_key_tolerates_small_position_drift():
    """重建的坐标可能有零点几 pt 的漂移——不能因此换身份。"""
    from services.drawing_archive import normalized_key

    a = normalized_key("spec_text", "x", {"title": "说明：", "x": 100.0, "y": 200.0})
    b = normalized_key("spec_text", "y", {"title": "说明：", "x": 103.4, "y": 197.2})
    assert a == b


@pytest.mark.unit
def test_different_blocks_keep_different_keys():
    """同图两块说明不能撞身份。"""
    from services.drawing_archive import normalized_key

    a = normalized_key("spec_text", "x", {"title": "设计说明", "x": 100.0, "y": 200.0})
    b = normalized_key("spec_text", "x", {"title": "施工说明", "x": 100.0, "y": 200.0})
    c = normalized_key("spec_text", "x", {"title": "设计说明", "x": 900.0, "y": 200.0})
    assert len({a, b, c}) == 3


@pytest.mark.unit
def test_spec_text_without_title_falls_back_to_content():
    """没有标题信息时退回原有行为，不能报错。"""
    from services.drawing_archive import normalized_key

    assert normalized_key("spec_text", "正文", None) == "spec_text:正文"


@pytest.mark.unit
def test_verified_block_wins_over_auto():
    """择优时人审版本胜出——这是「调整后直接参与后期模型和审图」的落点。"""
    from services.drawing_archive import effective_values

    rows = [
        {"category": "spec_text", "content": "原文", "source_kind": "auto",
         "is_active": True, "confidence": None,
         "value_json": {"title": "说明：", "x": 10.0, "y": 20.0}},
        {"category": "spec_text", "content": "人工校正", "source_kind": "verified",
         "is_active": True, "confidence": None,
         "value_json": {"title": "说明：", "x": 10.0, "y": 20.0}},
    ]
    values = effective_values(rows)
    assert len(values) == 1
    assert values[0]["content"] == "人工校正"


# ── 重跑不冲掉 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rebuild_preserves_verified_rows():
    """**实测缺陷**：重建删整个 `spec_text` 分类，人工修正一并消失。"""
    from services.drawing_spec_text import persist_spec_text

    deletes = []

    class DB:
        async def execute(self, sql, params=None):
            if "DELETE" in sql.upper():
                deletes.append(sql)

    await persist_spec_text(DB(), project_id="p", drawing_id="d", blocks=[])
    assert deletes, "没有清理旧的 auto 记录"
    assert "source_kind" in deletes[0], "删除没有排除 verified —— 人工修正会被冲掉"


@pytest.mark.unit
def test_rebuild_does_not_eat_its_own_output():
    """**反馈环**：重建读的是「这张图的全部档案行」，其中包含
    上一轮写进去的 `spec_text` 块本身——于是正文被重复追加，
    实测每跑一次翻一倍（「1.桩基说明…2.桩顶标高…1.桩基说明…」）。

    自产的分类必须排除在输入之外。
    """
    from services.drawing_spec_text import EXCLUDED_CATEGORIES, SPEC_CATEGORY

    assert SPEC_CATEGORY in EXCLUDED_CATEGORIES


@pytest.mark.unit
def test_previous_spec_block_is_not_read_back_as_a_token():
    from services.drawing_spec_text import tokens_from_archive

    tokens = tokens_from_archive([
        {"content": "说明：", "category": "note",
         "location_json": {"bbox": [10, 20, 70, 28]}},
        {"content": "上一轮拼好的整篇说明正文……", "category": "spec_text",
         "location_json": {"x": 10, "y": 30}},
    ])
    assert [t["text"] for t in tokens] == ["说明："]


@pytest.mark.unit
def test_position_is_stored_in_value_json_not_only_location():
    """`normalized_key` 只拿得到 `(category, content, value_json)` ——
    位置若只存在 `location_json` 里，auto 行算出的 key 就没有位置，
    与人审行（UI 会把整个 value_json 带回来）配不上对。

    **实测**：重跑后生效值变成 2 条，auto 复活并与 verified 并列。
    """
    from services.drawing_spec_text import spec_entry_params

    params = spec_entry_params("p", "d", {
        "title": "说明：", "text": "正文", "fragment_count": 3,
        "avg_line_chars": 20.0, "x": 812.3, "y": 445.6})
    import json
    value = json.loads(params["value_json"])
    assert value["x"] == 812.3 and value["y"] == 445.6


@pytest.mark.unit
def test_auto_and_verified_of_the_same_block_collapse_to_one():
    """同一块的 auto 与 verified 必须归一到同一条生效值。"""
    from services.drawing_archive import effective_values

    shared = {"title": "技术说明：", "x": 812.3, "y": 445.6}
    values = effective_values([
        {"category": "spec_text", "content": "原文", "source_kind": "auto",
         "is_active": True, "confidence": None,
         "value_json": {**shared, "fragment_count": 9}},
        {"category": "spec_text", "content": "原文\n【人工校正】",
         "source_kind": "verified", "is_active": True, "confidence": None,
         "value_json": {**shared, "fragment_count": 9}},
    ])
    assert len(values) == 1 and "人工校正" in values[0]["content"]
