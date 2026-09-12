"""给没记候选数的旧批次回填 `drawing_candidates`。

col3、wall3、beam3 是在生成器记下候选数之前出的批，manifest 里没有这一列，
层内加权就只能整批退回等权。回填靠重跑同一个识别调用数出每张图的候选数
（识别器未改动，结果可复现），再把列并进 manifest。

这里只测「并列」这一步的纯函数：列要插在 `MANIFEST_HEADER` 规定的位置，
缺数的图写 -1（回收端据此退回等权，而不是把缺数当成 0 候选）。
"""
from __future__ import annotations

import pytest


OLD_HEADER = "code\tgroup\tstratum\tdrawing_id\tsheet\tdup_of\tcapped\tcrop_pt\ttitle"


def _old_rows():
    return [
        {"code": "AAAA", "group": "kept", "stratum": "S", "drawing_id": "d1", "sheet": "S1",
         "dup_of": "", "capped": "0", "crop_pt": "0,0,1,1", "title": "一层"},
        {"code": "BBBB", "group": "blank", "stratum": "S", "drawing_id": "d2", "sheet": "S1",
         "dup_of": "", "capped": "0", "crop_pt": "0,0,1,1", "title": "二层"},
    ]


@pytest.mark.unit
def test_backfill_inserts_the_column_where_the_generator_puts_it():
    from scripts.model3d.gold_backfill_counts import merge_counts
    from scripts.model3d.gold_batch import MANIFEST_HEADER

    header, rows = merge_counts(OLD_HEADER.split("\t"), _old_rows(), {"d1": 812})
    assert "\t".join(header) == MANIFEST_HEADER, "回填后的列序必须与生成器一致"
    assert rows[0]["drawing_candidates"] == "812"


@pytest.mark.unit
def test_unknown_counts_are_minus_one_not_zero():
    """数不出来的写 -1 —— 写 0 会被当成「这张图没有候选」，那是另一回事。"""
    from scripts.model3d.gold_backfill_counts import merge_counts

    _h, rows = merge_counts(OLD_HEADER.split("\t"), _old_rows(), {"d1": 812})
    assert rows[1]["drawing_candidates"] == "-1"


@pytest.mark.unit
def test_backfill_is_idempotent():
    """已经有这一列的 manifest 再回填一次，不能重复插列。"""
    from scripts.model3d.gold_backfill_counts import merge_counts

    h1, r1 = merge_counts(OLD_HEADER.split("\t"), _old_rows(), {"d1": 812})
    h2, r2 = merge_counts(h1, r1, {"d1": 900})
    assert h2 == h1
    assert r2[0]["drawing_candidates"] == "900", "再回填以新数为准"
