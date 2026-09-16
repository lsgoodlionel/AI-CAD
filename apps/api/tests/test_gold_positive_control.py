"""正对照：抓「一律答不是」的判读者 —— 空白对照抓不到的那个方向。

col3（柱）空白对照 8/8、重测一致率 1.0、编号零编造全过，却在与 col4 相同的
8 张图上判出 1/13 而 col4 判出 12/21。少判没有对照，仪器就沉默。
"""
from __future__ import annotations

import pytest

from core.model3d.gold.batch_codes import make_codes
from core.model3d.gold.ingest import summarize
from core.model3d.gold.positives import HEADER, PositiveCell, format_positives, parse_positives
from core.model3d.gold.validity import MIN_POSITIVE_CELLS, positive_control_ok

CODES = make_codes(20, seed=17)


def _rows(n_pos: int):
    rows = [{"code": CODES[i], "group": "kept", "stratum": "A", "drawing_id": f"d{i}",
             "dup_of": "", "drawing_candidates": "10"} for i in range(4)]
    rows += [{"code": CODES[4 + i], "group": "pos", "stratum": "positive",
              "drawing_id": f"p{i}", "dup_of": "", "drawing_candidates": ""}
             for i in range(n_pos)]
    return rows


def _ans(code, ok):
    return {"id": code, "is_column": ok, "what": "" if ok else "text",
            "saw": f"看到 {code}", "confident": True}


@pytest.mark.unit
def test_all_negative_judge_fails_the_positive_control():
    rows = _rows(6)
    answers = [_ans(r["code"], False) for r in rows]        # 一律答「不是」
    s = summarize(rows, answers, field="is_column", weights={"A": 1.0})
    assert s["positive"] == (0, 6)
    ok, detail = positive_control_ok(n_pos=6, n_pos_judged_positive=0)
    assert not ok and "系统性少判" in detail


@pytest.mark.unit
def test_positive_control_tolerates_one_miss_and_stays_out_of_precision():
    rows = _rows(6)
    answers = [_ans(r["code"], True) for r in rows]          # 被测组四格全判真
    answers[4]["is_column"] = False                          # 正对照六格里失手一格
    answers[4]["what"] = "text"
    s = summarize(rows, answers, field="is_column", weights={"A": 1.0})
    assert s["positive"] == (5, 6)
    assert positive_control_ok(n_pos=6, n_pos_judged_positive=5)[0]
    assert s["raw_precision"] == 1.0, "正对照不进被测组的精确率"
    assert s["false_positive_labels"] == {}, "正对照失手的那格不算进误检构成"


@pytest.mark.unit
def test_too_few_positive_cells_proves_nothing():
    ok, detail = positive_control_ok(n_pos=MIN_POSITIVE_CELLS - 1,
                                     n_pos_judged_positive=MIN_POSITIVE_CELLS - 1)
    assert not ok and "证明不了" in detail


@pytest.mark.unit
def test_ingest_refuses_to_store_when_the_judge_under_calls():
    from scripts.model3d.gold_ingest import _gold_doc
    from core.model3d.gold.schema_check import check_file

    rows = _rows(6)
    answers = [_ans(r["code"], False) for r in rows]
    s = summarize(rows, answers, field="is_column", weights={"A": 1.0})
    doc = _gold_doc("t9", "columns", rows, {a["id"]: a for a in answers}, "is_column",
                    s, "空白对照 —", "正对照 0/6")
    assert [i for i in check_file("columns_t9_v1.json", doc) if i["level"] == "error"] == []
    unit = next(u for u in doc["units"] if u["source"]["group"] == "positive_control")
    verdicts = next(iter(unit["classes"].values()))["verdicts"]
    assert all(not v["ok"] for v in verdicts), "正对照判成「不是」= 失手，ok=False"


@pytest.mark.unit
def test_positives_registry_round_trips_without_storing_any_image():
    cells = [PositiveCell("d-1", (10.5, 20.0, 18.5, 28.0), (0.0, 0.0, 40.0, 40.0),
                          "col4:L9AK", "轴线交点上的灰色近方块")]
    text = format_positives(cells)
    assert text.startswith(HEADER)
    assert parse_positives(text) == cells
    assert parse_positives("# 注释\n\n" + text) == cells
    with pytest.raises(ValueError):
        parse_positives("d-1\t1,2,3\t0,0,1,1\n")
