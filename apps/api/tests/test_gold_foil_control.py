"""偏移对照：抓「一律答是」——空白对照抓不到的那一档。

锚点批实测 18/18 全判为对。红十字打在白纸上谁都能否掉（空白对照 3/3 全对），
所以那一关证明不了判读者在看位置。把标记**挪开一段**再问同一个问题，
答「对」就说明他没在看。
"""
from __future__ import annotations

import pytest

from core.model3d.gold.batch_codes import make_codes
from core.model3d.gold.ingest import summarize
from core.model3d.gold.validity import MIN_FOIL_CELLS, foil_control_ok

CODES = make_codes(20, seed=29)


def _rows(n_foil: int):
    rows = [{"code": CODES[i], "group": "kept", "stratum": "A", "drawing_id": f"d{i}",
             "dup_of": "", "drawing_candidates": "10"} for i in range(4)]
    rows += [{"code": CODES[4 + i], "group": "foil", "stratum": "A",
              "drawing_id": f"f{i}", "dup_of": "", "drawing_candidates": ""}
             for i in range(n_foil)]
    return rows


def _ans(code, ok):
    return {"id": code, "is_anchor": ok, "what": "" if ok else "not_an_intersection",
            "saw": f"看到 {code}", "confident": True}


@pytest.mark.unit
def test_always_yes_judge_is_caught_by_the_shifted_control():
    rows = _rows(6)
    answers = [_ans(r["code"], True) for r in rows]          # 一律答「对」
    s = summarize(rows, answers, field="is_anchor", weights={"A": 1.0})

    assert s["foil"] == (0, 6), "六格偏移对照全判成对 = 没在看位置"
    ok, detail = foil_control_ok(n_foil=6, n_foil_judged_negative=0)
    assert not ok and "没在看位置" in detail


@pytest.mark.unit
def test_a_reader_who_checks_position_passes():
    rows = _rows(6)
    answers = [_ans(r["code"], r["group"] == "kept") for r in rows]
    s = summarize(rows, answers, field="is_anchor", weights={"A": 1.0})

    assert s["foil"] == (6, 6)
    assert foil_control_ok(n_foil=6, n_foil_judged_negative=6)[0]
    assert s["raw_precision"] == 1.0, "偏移格不进被测组的精确率"


@pytest.mark.unit
def test_too_few_foils_prove_nothing():
    """锚点批实测只产出 3 格空白对照就被拦下；偏移对照同样要够数。"""
    ok, detail = foil_control_ok(n_foil=MIN_FOIL_CELLS - 1,
                                 n_foil_judged_negative=MIN_FOIL_CELLS - 1)
    assert not ok and "证明不了" in detail


@pytest.mark.unit
def test_shifted_mark_keeps_its_shape_and_reading_only_the_place_changes():
    """偏移只动位置：形状与读数不变，否则判读者能凭「长得不一样」认出对照组。"""
    from scripts.model3d.gold_batch import _foil_mark
    from core.model3d.gold.batch_design import Mark
    import random

    cross = Mark("cross", (100.0, 100.0, 156.0, 156.0), line=((128.0, 128.0),) * 2)
    crop = (0.0, 0.0, 336.0, 336.0)
    moved = _foil_mark(cross, crop, random.Random(7))

    assert moved.shape == "cross"
    width_before = cross.bbox[2] - cross.bbox[0]
    assert moved.bbox[2] - moved.bbox[0] == pytest.approx(width_before)
    dx = (moved.bbox[0] + moved.bbox[2]) / 2 - 128.0
    dy = (moved.bbox[1] + moved.bbox[3]) / 2 - 128.0
    assert (dx * dx + dy * dy) ** 0.5 == pytest.approx(336.0 * 0.35, rel=1e-6)


@pytest.mark.unit
def test_shifted_control_is_written_like_the_blank_one():
    from scripts.model3d.gold_ingest import _gold_doc
    from core.model3d.gold.schema_check import check_file

    rows = _rows(6)
    answers = [_ans(r["code"], r["group"] == "kept") for r in rows]
    s = summarize(rows, answers, field="is_anchor", weights={"A": 1.0})
    doc = _gold_doc("t3", "world_anchors", rows, {a["id"]: a for a in answers},
                    "is_anchor", s, "空白对照 —", "")

    assert [i for i in check_file("world_anchors_t3_v1.json", doc)
            if i["level"] == "error"] == []
    unit = next(u for u in doc["units"] if u["source"]["group"] == "shifted_control")
    verdicts = next(iter(unit["classes"].values()))["verdicts"]
    assert all(v["ok"] for v in verdicts), "判为「不对」即对照通过，ok=True"
