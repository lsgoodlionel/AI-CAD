"""档案原地重分类:分类规则改进后无需重抽 OCR 即可回灌。

**为什么需要**:今天改了 `classify_text`(`4F`/`大歌剧厅4F` → `level_name`、
单字不再判 room_name),但档案是抽取当时的分类快照:

| 内容 | 档案当前 | 应为 | 条数 |
|---|---|---|---:|
| `4F` / `3F` / `1F` | `other` | `level_name` | **307** |
| `大歌剧厅4F` 类 | `room_name` | `level_name` | **28** |

重抽 2309 张图要跑 OCR,很贵;而**分类只是对已存的 `content` 跑纯函数**,
原地重算即可。这让「改判据 → 全量回灌」变成一件便宜的事。

**两条不可越界的规则**:
1. 只重算 `extractor ∈ {ocr, vector_text}` —— `vlm` 走的是另一套语义,不归 `classify_text` 管
2. **绝不动 `source_kind='verified'`** —— 那是人工审核过的,规则改了也不能推翻人
"""
from __future__ import annotations

import pytest

from services.archive_reclassify import RECLASSIFIABLE_EXTRACTORS, plan_reclassify


def _row(rid: int, content: str, category: str, *,
         extractor: str = "ocr", source_kind: str = "auto") -> dict:
    return {"id": rid, "content": content, "category": category,
            "extractor": extractor, "source_kind": source_kind}


@pytest.mark.unit
def test_reclassifies_floor_marks_that_were_dumped_into_other():
    """实测缺口:307 条 `4F` 类落在 `other`。"""
    plan = plan_reclassify([_row(1, "4F", "other"), _row(2, "3F", "other")])
    assert {p["id"]: p["category"] for p in plan} == {1: "level_name",
                                                     2: "level_name"}


@pytest.mark.unit
def test_reclassifies_prefixed_floor_names_out_of_room_name():
    """实测缺口:28 条 `大歌剧厅4F` 类落在 `room_name`。"""
    plan = plan_reclassify([_row(1, "大歌剧厅4F", "room_name")])
    assert plan == [{"id": 1, "category": "level_name", "was": "room_name"}]


@pytest.mark.unit
def test_single_character_noise_is_moved_out_of_room_name():
    """图框会签栏被逐字拆开 —— 实测一张立面图 127 条。

    **归类口径已细化**：标题栏标签的用字（`校`/`设`/`计`/`单`/`位`）
    归 `title_block_label`，其余（`合`/`作`）仍归 `other`。
    两者都不是房间名 —— 本用例断言的是**移出 room_name**，
    而 `title_block_label` 比 `other` 多保留了「这是标题栏区域」这条信息，
    「图框字段区域记忆」正要靠它定位。
    """
    plan = plan_reclassify([_row(i, ch, "room_name")
                            for i, ch in enumerate("校合作设计单位")])
    assert all(p["category"] in ("other", "title_block_label") for p in plan)
    assert all(p["category"] != "room_name" for p in plan)
    assert len(plan) == 7


@pytest.mark.unit
def test_unchanged_rows_are_not_in_the_plan():
    """只产出**真的变了**的行 —— 否则 100 万行全量 UPDATE 毫无意义。"""
    assert plan_reclassify([_row(1, "16.200", "elevation"),
                            _row(2, "男卫", "room_name")]) == []


@pytest.mark.unit
def test_verified_rows_are_never_touched():
    """人工审核过的不能被规则改推翻(E1.5 auto/verified 分离)。"""
    assert plan_reclassify([_row(1, "4F", "other", source_kind="verified")]) == []


@pytest.mark.unit
def test_vlm_rows_are_not_reclassified():
    """`vlm` 走的是另一套语义,不归 `classify_text` 管。"""
    assert plan_reclassify([_row(1, "4F", "other", extractor="vlm")]) == []
    assert "vlm" not in RECLASSIFIABLE_EXTRACTORS


@pytest.mark.unit
def test_empty_content_is_skipped():
    assert plan_reclassify([_row(1, "", "other"), _row(2, "   ", "other")]) == []


@pytest.mark.unit
def test_plan_records_the_previous_category():
    """留下 `was`,便于事后核对改动是否合预期。"""
    plan = plan_reclassify([_row(1, "B1", "other")])
    assert plan[0]["was"] == "other" and plan[0]["category"] == "level_name"
