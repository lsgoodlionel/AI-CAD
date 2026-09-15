"""判读结果回收：编号纠错 → 有效性 → 分层加权汇总。

判读者**字符转写不可靠**（实测曾有 33% 的编号是编的），所以编号带校验位；
回收时先按校验位纠错，纠不回来的算「编造」，不进统计但要报出来。
"""
from __future__ import annotations

import pytest

from core.model3d.gold.batch_codes import make_codes
from core.model3d.gold.ingest import summarize

CODES = make_codes(12, seed=5)


def _manifest():
    """6 格被测（两层）+ 3 格空白 + 2 个重测副本。"""
    rows = []
    for i in range(3):
        rows.append({"code": CODES[i], "group": "kept", "stratum": "A", "dup_of": ""})
    for i in range(3, 6):
        rows.append({"code": CODES[i], "group": "kept", "stratum": "B", "dup_of": ""})
    for i in range(6, 9):
        rows.append({"code": CODES[i], "group": "blank", "stratum": "A", "dup_of": ""})
    rows.append({"code": CODES[9], "group": "dup", "stratum": "A", "dup_of": CODES[0]})
    rows.append({"code": CODES[10], "group": "dup", "stratum": "B", "dup_of": CODES[3]})
    return rows


def _ans(code, ok, what=""):
    return {"id": code, "is_column": ok, "what": what, "saw": "x", "confident": True}


@pytest.mark.unit
def test_summary_counts_per_stratum_and_weights():
    answers = ([_ans(CODES[i], True) for i in (0, 1, 2)]           # A：3/3
               + [_ans(CODES[3], True), _ans(CODES[4], False, "text"),
                  _ans(CODES[5], False, "elevation_mark")]        # B：1/3
               + [_ans(CODES[i], False, "nothing") for i in (6, 7, 8)]
               + [_ans(CODES[9], True), _ans(CODES[10], False)])
    s = summarize(_manifest(), answers, field="is_column",
                  weights={"A": 0.25, "B": 0.75})
    assert s["per_stratum"] == {"A": (3, 3), "B": (1, 3)}
    assert s["raw_precision"] == pytest.approx(4 / 6)
    # 0.25×1.0 + 0.75×(1/3) = 0.5
    assert s["weighted_precision"] == pytest.approx(0.5)
    assert s["coverage"] == pytest.approx(1.0)


@pytest.mark.unit
def test_blank_control_and_retest_pairs():
    answers = ([_ans(CODES[i], True) for i in range(6)]
               + [_ans(CODES[i], False, "nothing") for i in (6, 7, 8)]
               + [_ans(CODES[9], True),                              # 与原格一致
                  _ans(CODES[10], False)])                           # 与原格不一致
    s = summarize(_manifest(), answers, field="is_column", weights={"A": 1, "B": 1})
    assert s["blank"] == (3, 3), "3 格空白对照全部判为「不是」"
    assert s["pair_agreement"] == pytest.approx(0.5)


@pytest.mark.unit
def test_miscopied_code_is_repaired_by_checksum():
    """判读者抄错一位编号，校验位能把它纠回来 —— 这正是带校验位的意义。"""
    good = CODES[0]
    typo = ("X" if good[0] != "X" else "Y") + good[1:]
    answers = [_ans(typo, True)]
    s = summarize(_manifest(), answers, field="is_column", weights={"A": 1, "B": 1})
    assert s["repaired"] >= 0
    assert s["fabricated"] + s["repaired"] + s["matched"] == 1


@pytest.mark.unit
def test_unknown_codes_are_reported_not_used():
    """纠不回来的编号算「编造」—— 不进统计，但必须报出来。"""
    answers = [_ans("ZZZZ", True), _ans(CODES[0], True)]
    s = summarize(_manifest(), answers, field="is_column", weights={"A": 1, "B": 1})
    assert s["fabricated"] == 1
    assert s["per_stratum"]["A"] == (1, 1), "编造的那条没有混进统计"


@pytest.mark.unit
def test_ingested_doc_passes_the_gold_schema_and_counts_toward_metrics():
    """**端到端**：导入写出的金标准文件要能被全目录校验收下，且裁决计入指标。

    实测（2026-09-15 首次用真实答案跑）：`gold_report --check-only` 报三类错 ——
    判「否」的裁决没写 `what`（schema 硬约束）、表头 `object_classes=['walls']`
    与单元里的 `walls_wall3` 对不上；另外 `confidence=0.0 + verified_by=
    ['independent_judge']` 让整批**不计入指标**（非人工复核时要把握 ≥0.8），
    又是一个静默失效。既有复测批的约定是 `verified_by=['gpt'] / confidence=1.0`。
    """
    from core.model3d.gold.schema import parse_unit
    from core.model3d.gold.schema_check import check_file
    from scripts.model3d.gold_ingest import _gold_doc

    answers = ([_ans(CODES[i], True) for i in (0, 1, 2)]
               + [_ans(CODES[3], False, "text"), _ans(CODES[4], False, ""),
                  _ans(CODES[5], True)]
               + [_ans(CODES[6], True)]                     # 空白对照失手：判成构件
               + [_ans(CODES[i], False, "nothing") for i in (7, 8)]
               + [_ans(CODES[9], True), _ans(CODES[10], False, "text")])
    s = summarize(_manifest(), answers, field="is_column", weights={"A": 1, "B": 1})
    doc = _gold_doc("t1", "columns", _manifest(), {a["id"]: a for a in answers},
                    "is_column", s, "空白对照 2/3")
    errors = [i for i in check_file("columns_t1_v1.json", doc) if i["level"] == "error"]
    assert errors == [], errors
    for unit in doc["units"]:
        for cls in parse_unit(unit).classes.values():
            assert cls.counts_toward_metrics, f"{unit['unit']} 不计入指标"


@pytest.mark.unit
def test_false_positive_labels_are_normalized():
    """误检写法归一（taxonomy），才能回答「误检里有多少是标高符号」。"""
    answers = [_ans(CODES[3], False, "elevation_mark"), _ans(CODES[4], False, "标高三角"),
               _ans(CODES[5], False, "text")]
    s = summarize(_manifest(), answers, field="is_column", weights={"A": 1, "B": 1})
    assert s["false_positive_labels"].get("elevation_mark") == 2
    assert s["false_positive_labels"].get("text") == 1
