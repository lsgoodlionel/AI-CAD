"""「闸删掉的」一组：量一道新闸的误删率（柱孤立度闸的验证批要用）。

判读者照旧不知道哪格来自哪组；回收时：
- 被测组（kept）照旧算精确率 —— 这是闸之后的精确率；
- 闸删组（gated）数判读者判为真柱的格数 —— 那是**误删**。

写成金标准时沿用 col2（`columns_final_v1.json`）的约定：单元组名 `dropped_by_gate`，
`ok` = 闸删对了（判读为「不是柱」），与空白对照同一方向。
"""
from __future__ import annotations

import pytest

from core.model3d.gold.batch_codes import make_codes
from core.model3d.gold.ingest import summarize

CODES = make_codes(10, seed=13)


def _manifest():
    rows = [{"code": CODES[i], "group": "kept", "stratum": "A", "drawing_id": f"d{i}",
             "dup_of": "", "drawing_candidates": "10"} for i in range(4)]
    rows += [{"code": CODES[i], "group": "gated", "stratum": "A", "drawing_id": f"d{i}",
              "dup_of": "", "drawing_candidates": "10"} for i in range(4, 8)]
    return rows


def _ans(code, ok, what=""):
    return {"id": code, "is_column": ok, "what": what, "saw": "x", "confident": True}


def _answers():
    return ([_ans(CODES[0], True), _ans(CODES[1], False, "text"),
             _ans(CODES[2], False, "hatch"), _ans(CODES[3], True)]
            + [_ans(CODES[4], True)]                                  # 闸误删了一根真柱
            + [_ans(CODES[i], False, "text") for i in (5, 6, 7)])


@pytest.mark.unit
def test_gated_group_counts_wrongly_dropped_columns_and_leaves_precision_alone():
    s = summarize(_manifest(), _answers(), field="is_column", weights={"A": 1.0})
    assert s["gated"] == (1, 4), "4 格闸删组里 1 格判为真柱 = 误删 1"
    assert s["raw_precision"] == pytest.approx(0.5), "精确率只看被测组"
    assert sum(s["false_positive_labels"].values()) == 2, "闸删组的格子不算进误检构成"


@pytest.mark.unit
def test_gated_group_is_written_like_columns_final():
    from core.model3d.gold.schema import parse_unit
    from core.model3d.gold.schema_check import check_file
    from scripts.model3d.gold_ingest import _gold_doc

    s = summarize(_manifest(), _answers(), field="is_column", weights={"A": 1.0})
    doc = _gold_doc("t2", "columns", _manifest(), {a["id"]: a for a in _answers()},
                    "is_column", s, "空白对照 —")
    assert [i for i in check_file("columns_t2_v1.json", doc) if i["level"] == "error"] == []
    unit = next(u for u in doc["units"] if u["source"]["group"] == "dropped_by_gate")
    verdicts = next(iter(parse_unit(unit).classes.values())).verdicts
    assert sorted(v.ok for v in verdicts) == [False, True, True, True], \
        "ok = 闸删对了；判为真柱的那格是 ok=False（误删）"


@pytest.mark.unit
def test_gate_split_uses_the_isolation_filter():
    from scripts.model3d.gold_batch import _gate_split

    def sq(cx, side):
        h = side / 2
        return {"outline": [[cx - h, -h], [cx + h, -h], [cx + h, h], [cx - h, h]]}

    elems = [sq(0, 0.6), sq(8.4, 0.6)] + [sq(30 + i * 0.33, 0.3) for i in range(4)]
    kept, gated = _gate_split(elems, 3.0)
    assert kept == [0, 1]
    assert gated == [2, 3, 4, 5]
    assert _gate_split(elems, None) == (list(range(6)), [])
