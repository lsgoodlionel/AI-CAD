"""比例可信度证据层（工作包 B）。

**为什么要这一层。** `drawing_transform.confidence` 旧公式是
`带标签轴线数/轴线总数 × 比例是否常用值`——它衡量的是**轴号识别质量**，
与**比例对不对**无关。金标准 56 条独立裁决实测：

    confidence = 1.00  →  合理率 24%
    confidence < 1.00  →  合理率 37%

**置信度携带的是负信息。** 梁批独立同向佐证：有「满分置信」变换记录的图
梁精确率 44%，无记录的 78%。

本文件锁住新证据层的行为。判据与实测依据写在
`core/model3d/scale_evidence.py` 的模块文档里，这里只断言行为。
"""
from __future__ import annotations

import pytest

from core.model3d.scale_evidence import (
    MAX_SHEET_COVERAGE_M,
    MIN_SHEET_COVERAGE_M,
    PT_TO_MM,
    WEIGHT_COVERAGE,
    WEIGHT_PRINTED,
    WEIGHT_STANDARD,
    evaluate,
    printed_denominators,
)


def _scale(denominator: float) -> float:
    """图纸比例分母 → scale_m_pt（1pt = 25.4/72 mm，物理关系精确）。"""
    return denominator * PT_TO_MM / 1000.0


A0_W, A0_H = 3370.0, 2384.0   # A0 图幅（1189×841 mm）的排版点尺寸


# ── 图面印刷比例的提取 ────────────────────────────────────────────

@pytest.mark.unit
def test_printed_denominators_reads_the_scale_string():
    """`1:100` / 全角冒号 / 带空格，都要认得出。"""
    votes = printed_denominators([
        (10.0, 10.0, "一层平面图 1:100"),
        (20.0, 20.0, "剖面图 1：50"),
        (30.0, 30.0, "详图 1: 20"),
    ])
    assert set(votes) == {100, 50, 20}


@pytest.mark.unit
def test_printed_denominators_drops_values_outside_the_national_table():
    """`1:7`、`1:123` 不在 GB/T 50001 §6.0.4 表里，是别的数字被误读成比例。

    实测档案里确有 `i=1:8`（坡度）、`1:0`（OCR 噪声）这类命中，
    不过滤就会把坡度当成比例尺。
    """
    votes = printed_denominators([
        (10.0, 10.0, "i=1:8"),
        (11.0, 11.0, "1:0"),
        (12.0, 12.0, "1:123"),
        (13.0, 13.0, "1:100"),
    ])
    assert set(votes) == {100}


@pytest.mark.unit
def test_title_block_votes_outweigh_the_middle_of_the_sheet():
    """图框带里的比例是**这张图的主比例**，图面中间的是详图局部比例。

    与 `services.scale_candidates.in_title_block` 同源判据：
    图框在图纸右侧或下侧。一张图上常有多个 `1:N`，不分位置直接数票，
    详图那几个会把主比例压掉。
    """
    texts = [
        (100.0, 100.0, "节点一 1:10"),
        (200.0, 200.0, "节点二 1:10"),
        (300.0, 300.0, "节点三 1:10"),
        (A0_W * 0.95, A0_H * 0.5, "1:150"),      # 右侧图框带
    ]
    votes = printed_denominators(texts, page_w=A0_W, page_h=A0_H)
    assert max(votes, key=lambda d: votes[d]) == 150


# ── 综合证据分 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_scale_matching_the_printed_value_scores_high():
    got = evaluate(_scale(150), texts=[(A0_W * 0.95, A0_H * 0.5, "1:150")],
                   page_w_pt=A0_W, page_h_pt=A0_H)
    assert got.score is not None and got.score > 0.9


@pytest.mark.unit
def test_scale_contradicting_the_printed_value_scores_low():
    """图上白纸黑字写着 1:150，系统算出 1:10 —— 这是**最强的反证**。

    旧公式在这种情况下照样给 1.00（10 是常用值、轴号又全识别出来了）。
    """
    got = evaluate(_scale(10), texts=[(A0_W * 0.95, A0_H * 0.5, "1:150")],
                   page_w_pt=A0_W, page_h_pt=A0_H)
    assert got.score is not None and got.score < 0.4


@pytest.mark.unit
def test_standard_value_alone_cannot_outweigh_a_contradicted_printed_scale():
    """「是常用比例」是**必要不充分**条件，不能救回被印刷比例否掉的值。

    实测依据：金标准四个分层里，「满分置信·常用比例」这一层最差
    （14.3%，n=14），比「中等置信」（40%）还低 —— 常用值这条证据
    如果权重太大，就会重演旧公式的错误。
    """
    contradicted = evaluate(
        _scale(100), texts=[(A0_W * 0.95, A0_H * 0.5, "1:20")],
        page_w_pt=A0_W, page_h_pt=A0_H)
    no_evidence = evaluate(_scale(100), texts=[], page_w_pt=A0_W, page_h_pt=A0_H)
    assert contradicted.score < no_evidence.score
    assert WEIGHT_PRINTED > WEIGHT_STANDARD + WEIGHT_COVERAGE


@pytest.mark.unit
def test_missing_printed_scale_is_visible_not_silently_counted_as_agreement():
    """**降级必须可见**：图上没写比例 ≠ 比例对。

    这条是项目硬规矩之一。没有证据时该说没有证据，
    而不是把「没找到反证」记成「有正证」。
    """
    got = evaluate(_scale(100), texts=[], page_w_pt=A0_W, page_h_pt=A0_H)
    printed = next(e for e in got.items if e.name == "printed_scale")
    assert printed.available is False
    assert "printed_scale" in got.reason()


@pytest.mark.unit
def test_reason_names_every_evidence_item():
    """理由串要把每条证据都写出来 —— 分数不可解释就没法被质疑。"""
    got = evaluate(_scale(100), texts=[(A0_W * 0.95, 10.0, "1:100")],
                   page_w_pt=A0_W, page_h_pt=A0_H)
    for name in ("printed_scale", "standard_denominator", "sheet_coverage"):
        assert name in got.reason()


# ── 图幅覆盖：物理可能范围 ────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("coverage_m,expected_ok", [
    (0.05, False),       # 比 A4×1:1 还小，任何合法图幅×比例都到不了
    (0.7, True),         # A4 × 1:3 就有 0.63 米 —— **合法**，见下面的局限测试
    (60.0, True),        # A0 × 1:150 = 178 米图幅、判读格 60 米，正常大平面
    (2378.0, True),      # A0 × 1:2000，§6.0.4 表的下限比例，总平面图
    (5829.0, False),     # 实测 `NWPV`：1:4861，超出任何合法图幅×比例组合
])
def test_sheet_coverage_flags_physically_impossible_scales(coverage_m, expected_ok):
    """图幅 × 比例 = 这张纸能画下多少米。

    GB/T 50001 §6.0.4 的比例上下限（1:1 ~ 1:2000）配 A4~A0 图幅
    （210 ~ 1189 mm），物理可能范围是 0.21 ~ 2378 米。
    超出这个范围的比例，**不需要看图就知道错了**。
    """
    page_w = 1000.0
    scale = coverage_m / page_w          # 让这张纸恰好覆盖 coverage_m 米
    got = evaluate(scale, texts=[], page_w_pt=page_w, page_h_pt=page_w * 0.7)
    cov = next(e for e in got.items if e.name == "sheet_coverage")
    assert cov.available is True
    assert (cov.score > 0.9) is expected_ok


@pytest.mark.unit
def test_sheet_coverage_cannot_catch_a_plausible_looking_but_wrong_scale():
    """**记下这条证据够不着的地方。**

    实测 `WMY4`「1区立柱桩及钢立柱平面布置图」被算成 1:2.4，
    A1 图幅只覆盖 2.1 米 —— 一张桩位平面图不可能只有 2 米宽。
    但 A4 图幅画 1:2 的节点详图**本来就只覆盖 0.42 米**，
    所以「物理可能区间」这条证据判不了它。要判它得知道图种，
    那是另一条证据（图名 → 图种 → 常用比例档），本轮没做。
    """
    a1_long_pt = 2384.0
    got = evaluate(2.4 * PT_TO_MM / 1000.0, texts=[],
                   page_w_pt=a1_long_pt, page_h_pt=1684.0)
    cov = next(e for e in got.items if e.name == "sheet_coverage")
    assert cov.score == 1.0, "物理可能区间放得过它——这是已知的能力边界"


@pytest.mark.unit
def test_standard_value_alone_is_not_enough_to_produce_a_score():
    """只剩「分母是表里的值」这一条时**不给分数**。

    这正是旧公式的病：满足一个**必要不充分**条件就拿满分。
    实测「满分置信·常用比例」分层合理率 14.3%，是四层里最低的。
    """
    got = evaluate(_scale(100), texts=None, page_w_pt=None, page_h_pt=None)
    standard = next(e for e in got.items if e.name == "standard_denominator")
    assert standard.available is True, "这条证据本身是可得的"
    assert got.score is None, "但它一条撑不起一个置信度"


@pytest.mark.unit
def test_coverage_bounds_come_from_the_standard_not_from_the_gold():
    """边界必须能从国标推出来，不能是回测拟合出来的。

    A4（210mm）× 1:1 = 0.21 米；A0（1189mm）× 1:2000 = 2378 米。
    """
    assert MIN_SHEET_COVERAGE_M == pytest.approx(0.21, abs=0.02)
    assert MAX_SHEET_COVERAGE_M == pytest.approx(2378.0, rel=0.02)


# ── 分数的边界行为 ────────────────────────────────────────────────

@pytest.mark.unit
def test_score_is_none_when_no_evidence_is_available_at_all():
    """一条证据都没有时给 `None`，不是 0 也不是 0.5。

    0 会让下游把这张图当成「已证伪」，0.5 会让它当成「半可信」——
    两者都是在没有信息时假装有信息。
    """
    got = evaluate(_scale(100), texts=None, page_w_pt=None, page_h_pt=None)
    assert got.score is None


@pytest.mark.unit
def test_score_stays_within_zero_and_one():
    for denominator in (1, 10, 100, 150, 2000, 100000):
        got = evaluate(_scale(denominator), texts=[(10.0, 10.0, "1:100")],
                       page_w_pt=A0_W, page_h_pt=A0_H)
        assert got.score is None or 0.0 <= got.score <= 1.0


@pytest.mark.unit
def test_invalid_scale_yields_no_score():
    """比例为 0 / 负数 / None 时不给分数 —— 那不是「置信度低」，是没有比例。"""
    for bad in (0.0, -1.0, None):
        assert evaluate(bad, texts=[(10.0, 10.0, "1:100")],
                        page_w_pt=A0_W, page_h_pt=A0_H).score is None


# ── 与 drawing_transform 的接线 ───────────────────────────────────

@pytest.mark.unit
def test_transform_keeps_the_old_value_as_label_confidence(monkeypatch):
    """旧 confidence 衡量的**轴号识别质量**本身是有用的，只是不该叫置信度。

    换掉 confidence 的同时必须把旧值留在 `label_confidence`，
    否则「轴号识别得怎么样」这个信息就没了。
    """
    from services import drawing_transform as dt

    class _Geom:
        lines: list = []
        texts: list = []
        page_w = A0_W
        page_h = A0_H

    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_axes",
        lambda *a, **k: ([("1", 100.0)], [("", 200.0)], None))
    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_scale",
        lambda *a, **k: (_scale(100), False))
    monkeypatch.setattr(
        "core.model3d.element_recognizer._origin_pt", lambda *a, **k: (0.0, 0.0))

    got = dt.transform_from_geometry(_Geom())
    assert got is not None
    # 两条轴线只有一条带标签 → 旧公式 0.5 × 1.0（100 是常用比例）
    assert got.label_confidence == pytest.approx(0.5)


@pytest.mark.unit
def test_weak_evidence_can_only_lower_confidence_never_raise_it(monkeypatch):
    """只有必要不充分的佐证时，confidence 不得高于旧值。

    **实测这条不写会出事**：全库 2142 条变换在没有 OCR 文本注入时
    只剩「图幅覆盖」一条证据，1866 条会拿到 1.00，
    `scale_gate` 判为可信的从 1290 涨到 **1891** —— 比旧公式更糟。
    用一个必要条件当背书，正是旧公式的病。
    """
    from services import drawing_transform as dt

    class _Geom:
        lines: list = []
        texts: list = []          # 没有印刷比例可读
        page_w = A0_W
        page_h = A0_H

    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_axes",
        # 两条轴线只有一条带标签 → 旧值 0.5
        lambda *a, **k: ([("1", 100.0)], [("", 200.0)], None))
    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_scale",
        lambda *a, **k: (_scale(100), False))
    monkeypatch.setattr(
        "core.model3d.element_recognizer._origin_pt", lambda *a, **k: (0.0, 0.0))

    got = dt.transform_from_geometry(_Geom())
    assert got is not None
    assert got.confidence <= got.label_confidence
    assert got.confidence == pytest.approx(0.5)


@pytest.mark.unit
def test_transform_confidence_uses_the_printed_scale_when_it_is_available(monkeypatch):
    """图上写着 1:150 而系统算出 1:100 —— 新 confidence 必须低于一致时。"""
    from services import drawing_transform as dt

    class _Geom:
        def __init__(self, text: str) -> None:
            self.lines: list = []
            self.texts = [(A0_W * 0.95, A0_H * 0.5, text)]
            self.page_w = A0_W
            self.page_h = A0_H

    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_axes",
        lambda *a, **k: ([("1", 100.0)], [("A", 200.0)], None))
    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_scale",
        lambda *a, **k: (_scale(100), False))
    monkeypatch.setattr(
        "core.model3d.element_recognizer._origin_pt", lambda *a, **k: (0.0, 0.0))

    agree = dt.transform_from_geometry(_Geom("1:100"))
    clash = dt.transform_from_geometry(_Geom("1:150"))
    assert agree is not None and clash is not None
    assert agree.confidence > clash.confidence
    # 旧公式对这两张图给的是**同一个满分** —— 那正是要修的病
    assert agree.label_confidence == clash.label_confidence == pytest.approx(1.0)
