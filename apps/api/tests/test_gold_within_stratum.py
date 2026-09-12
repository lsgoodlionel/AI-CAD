"""层内按候选量加权 —— 修掉「稀疏图在层内被高估」。

**怎么发现的**：col3 批目检时，一页 11 格里 3 格落在标题栏（logo 字母、手写
日期）。随后量了全库柱候选的位置密度：右下角（占页面 4.5%）只装着 **0.8%**
的候选 —— 标题栏里的假柱在总体里其实很少。

矛盾出在抽样上：生成器每图至多取 2 格、**不论这张图有多少候选**。一张只有
3 个 logo 字母被当成柱的图，与一张有 800 根柱的图，各贡献 2 格。于是层内
稀疏图被严重高估，而用户看到的是**候选量**，由大图主导。

跨层已按语料加权（教训②），层内也要：每格的权重 =
所在图纸候选数 ÷ 从该图取的格数。
"""
from __future__ import annotations

import pytest

from core.model3d.gold.batch_codes import make_codes
from core.model3d.gold.ingest import summarize


def _batch(cells):
    """cells: [(stratum, drawing_id, drawing_candidates, verdict)] → (manifest, answers)"""
    codes = make_codes(len(cells), seed=7)
    manifest, answers = [], []
    for code, (stratum, did, n, ok) in zip(codes, cells):
        row = {"code": code, "group": "kept", "stratum": stratum,
               "drawing_id": did, "dup_of": ""}
        if n is not None:
            row["drawing_candidates"] = str(n)
        manifest.append(row)
        answers.append({"id": code, "is_column": ok, "what": "" if ok else "text"})
    return manifest, answers


@pytest.mark.unit
def test_cells_are_weighted_by_their_drawings_candidate_volume():
    """稀疏图 A：3 个候选，取 2 格都错；大图 B：800 个候选，取 2 格都对。

    不加权是 50%；按候选量加权 = (0×3 + 1×800) / 803 ≈ 99.6%。
    """
    manifest, answers = _batch([
        ("S", "A", 3, False), ("S", "A", 3, False),
        ("S", "B", 800, True), ("S", "B", 800, True)])
    s = summarize(manifest, answers, field="is_column", weights={"S": 1.0})
    assert s["raw_precision"] == pytest.approx(0.5)
    assert s["within_stratum_weighted"] is True
    assert s["weighted_precision"] == pytest.approx(800 / 803, abs=1e-6)


@pytest.mark.unit
def test_missing_counts_fall_back_to_equal_weights_and_say_so():
    """有格缺候选数就整批退回等权 —— 不能一半加权一半不加，并且要标出来。"""
    manifest, answers = _batch([
        ("S", "A", 3, False), ("S", "B", None, True)])
    s = summarize(manifest, answers, field="is_column", weights={"S": 1.0})
    assert s["within_stratum_weighted"] is False
    assert s["weighted_precision"] == pytest.approx(0.5)


@pytest.mark.unit
def test_cells_from_the_same_drawing_share_its_weight():
    """同一张图取了 2 格，两格平分这张图的候选量 —— 否则大图被重复计权。"""
    manifest, answers = _batch([
        ("S", "A", 100, True), ("S", "A", 100, False),     # A 取 2 格：各占 50
        ("S", "B", 100, True)])                            # B 取 1 格：占 100
    s = summarize(manifest, answers, field="is_column", weights={"S": 1.0})
    # (50·1 + 50·0 + 100·1) / 200 = 0.75
    assert s["weighted_precision"] == pytest.approx(0.75)


@pytest.mark.unit
def test_generator_manifest_records_drawing_candidates():
    """生成器要把每格所在图纸的候选数写进 manifest —— 不然每批都得事后回填。"""
    from scripts.model3d.gold_batch import MANIFEST_HEADER, Cell, manifest_row

    assert "drawing_candidates" in MANIFEST_HEADER.split("\t")
    cell = Cell("ABCD", "kept", "S", "d1", "某图", (0.0, 0.0, 1.0, 1.0), None,
                drawing_candidates=812)
    row = dict(zip(MANIFEST_HEADER.split("\t"), manifest_row(cell, 3).split("\t")))
    assert row["drawing_candidates"] == "812"
    assert row["sheet"] == "S3"
