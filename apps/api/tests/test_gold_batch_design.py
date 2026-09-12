"""金标准批次设计：把三条已知教训焊进生成器，而不是靠每次记得。

    教训①  渲染分辨率要匹配问题所在的尺度
            —— 比例批把 1872×1324 的裁图缩到 420px，门只剩 7px，判据要用的
               参照物根本看不见。**裁图必须按原生分辨率渲染到格子大小**。
    教训②  分层抽样必然放大稀有类，样本内占比 ≠ 语料占比
            —— 所以每批要报**语料加权**的精确率，并报它**覆盖了多少语料**。
    信度    判读者重测信度 74%，差异小于这个量级的两个数不该当成不同
            —— 所以每批**自带重测对**，测它自己这一批的信度。

判据的「照抄」也程序化：从 `CRITERIA.md` 按小节抽取，不再手抄 ——
手抄出过两次不可比（柱 0.59 vs 0.22、68% vs 22%）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.model3d.gold.batch_design import (
    Candidate,
    crop_box_pt,
    criteria_section,
    plan_duplicates,
    render_dpi_for_crop,
    stratify,
)
from core.model3d.gold.validity import (
    blank_control_ok,
    pair_agreement,
    weighted_precision,
)

CRITERIA = Path(__file__).resolve().parents[1] / "data/model3d/gold/CRITERIA.md"


# ── 教训①：裁图原生分辨率 ────────────────────────────────────────

@pytest.mark.unit
def test_crop_renders_natively_to_cell_size():
    """按裁框的页面点数算 DPI，让裁图**恰好**是格子大小 —— 不缩放。"""
    crop_w_pt = 120.0                         # 裁框宽 120 点
    dpi, capped = render_dpi_for_crop(crop_w_pt, crop_w_pt, cell_px=480)
    rendered_px = crop_w_pt / 72.0 * dpi
    assert abs(rendered_px - 480) < 1.0, f"应渲染成 480px，实得 {rendered_px:.1f}"
    assert capped is False


@pytest.mark.unit
def test_huge_crop_is_capped_and_says_so():
    """裁框大到需要超高 DPI 反过来（极小裁框）会爆 —— 上限要兜住并报出来。"""
    dpi, capped = render_dpi_for_crop(2.0, 2.0, cell_px=480, max_dpi=600)
    assert dpi == 600 and capped is True, "2 点的裁框要 17280 DPI，必须封顶且可见"


@pytest.mark.unit
def test_crop_keeps_context_around_small_candidates():
    """小候选也要留够上下文 —— 判「是不是柱」要看得见轴线交点和旁边的编号。

    裁框取「构件尺寸 × rel」与「ctx_m 换算的点数」中较大者，
    否则一个 0.1m 的碎块只会得到 0.4m 的上下文，判读者无从判断。
    """
    elem = (500.0, 500.0, 502.0, 502.0)           # 2 点见方的小碎块
    scale_m_pt = 0.0529                           # 1:150
    x0, y0, x1, y1 = crop_box_pt(elem, scale_m_pt, page_w=3000, page_h=2000,
                                 ctx_m=5.0, rel=4.0)
    side_m = (x1 - x0) * scale_m_pt
    assert side_m >= 4.9, f"上下文只有 {side_m:.2f}m，看不到周围"


@pytest.mark.unit
def test_crop_stays_on_the_page():
    """贴边的候选，裁框不能伸出页面 —— 伸出去的部分渲染出来是空白，会误导判读。"""
    elem = (2.0, 2.0, 12.0, 12.0)
    x0, y0, x1, y1 = crop_box_pt(elem, 0.0353, page_w=842, page_h=595, ctx_m=5.0)
    assert x0 >= 0 and y0 >= 0 and x1 <= 842 and y1 <= 595


@pytest.mark.unit
def test_crop_survives_a_wrong_scale():
    """比例错了（全库只有 68.6% 的系统比例与图上印刷值一致），裁框不能失控。

    比例大 100 倍时，「5 米」换算成的点数会缩成一个点 —— 靠 `rel` 与
    `min_pt` 兜住，裁框仍然看得见构件本身。
    """
    elem = (500.0, 500.0, 520.0, 520.0)
    x0, y0, x1, y1 = crop_box_pt(elem, 5.0, page_w=3000, page_h=2000, ctx_m=5.0)
    assert (x1 - x0) >= 40, "比例错到离谱时，裁框也不能小到看不见构件"


# ── 教训②：分层抽样 ────────────────────────────────────────────

def _cands(stratum: str, n_drawings: int, per: int) -> list[Candidate]:
    return [Candidate(stratum=stratum, drawing_id=f"{stratum}-d{d}",
                      key=f"{stratum}-d{d}-{i}", payload={})
            for d in range(n_drawings) for i in range(per)]


@pytest.mark.unit
def test_stratify_fills_each_stratum_quota():
    pool = _cands("A", 10, 5) + _cands("B", 10, 5) + _cands("C", 2, 1)
    picked = stratify(pool, per_stratum=6, per_drawing=2, seed=1)
    by = {}
    for c in picked:
        by[c.stratum] = by.get(c.stratum, 0) + 1
    assert by["A"] == 6 and by["B"] == 6
    assert by["C"] == 2, "稀有层有多少取多少，不因为凑不满配额就丢掉"


@pytest.mark.unit
def test_stratify_caps_cells_per_drawing():
    """每张图至多取 per_drawing 格 —— YOLO 批出过「一张图占 15 格」的集中。"""
    pool = _cands("A", 2, 50)
    picked = stratify(pool, per_stratum=20, per_drawing=2, seed=1)
    per_d = {}
    for c in picked:
        per_d[c.drawing_id] = per_d.get(c.drawing_id, 0) + 1
    assert max(per_d.values()) <= 2
    assert len(picked) == 4, "2 张图 × 每张 2 格"


@pytest.mark.unit
def test_stratify_is_reproducible():
    pool = _cands("A", 10, 5) + _cands("B", 10, 5)
    a = [c.key for c in stratify(pool, per_stratum=6, per_drawing=2, seed=7)]
    b = [c.key for c in stratify(pool, per_stratum=6, per_drawing=2, seed=7)]
    assert a == b


@pytest.mark.unit
def test_weighted_precision_corrects_for_oversampled_strata():
    """稀有层被分层抽样放大了，加权要把它压回语料里的真实分量。

    构造：层 A 在语料里占 90%、精确率 80%；层 B 占 10%、精确率 20%。
    各抽 10 格时原始精确率是 50%，语料加权应是 0.9×0.8 + 0.1×0.2 = 74%。
    """
    per = {"A": (8, 10), "B": (2, 10)}
    weights = {"A": 0.9, "B": 0.1}
    p, coverage = weighted_precision(per, weights)
    assert p == pytest.approx(0.74, abs=1e-9)
    assert coverage == pytest.approx(1.0)


@pytest.mark.unit
def test_coverage_reports_unsampled_strata():
    """没抽到的层不能被悄悄当成「和别的层一样」—— 要报出覆盖率。"""
    per = {"A": (8, 10)}
    weights = {"A": 0.6, "B": 0.4}
    p, coverage = weighted_precision(per, weights)
    assert coverage == pytest.approx(0.6), "只覆盖了 60% 的语料"
    assert p == pytest.approx(0.8), "精确率只对覆盖到的那部分成立"


# ── 信度：批内重测对 ────────────────────────────────────────────

@pytest.mark.unit
def test_duplicates_land_on_different_sheets():
    """重测对的两份必须在不同页 —— 同一页上并排，判读者会照抄自己。"""
    keys = [f"k{i}" for i in range(40)]
    sheet_of = {k: i // 12 for i, k in enumerate(keys)}      # 每页 12 格
    pairs = plan_duplicates(keys, sheet_of=sheet_of, fraction=0.1,
                            n_sheets=4, seed=3)
    assert len(pairs) == 4, "40 格的 10%"
    for original, dup_sheet in pairs:
        assert dup_sheet != sheet_of[original]


@pytest.mark.unit
def test_pair_agreement():
    answers = {"a1": True, "a2": True, "b1": False, "b2": True, "c1": True, "c2": True}
    pairs = [("a1", "a2"), ("b1", "b2"), ("c1", "c2")]
    assert pair_agreement(answers, pairs) == pytest.approx(2 / 3)


@pytest.mark.unit
def test_pair_agreement_skips_missing_answers():
    """判读者漏答的对不计入分母 —— 缺答不等于不一致。"""
    answers = {"a1": True, "a2": True, "b1": False}
    assert pair_agreement(answers, [("a1", "a2"), ("b1", "b2")]) == pytest.approx(1.0)


# ── 有效性：空白对照 ────────────────────────────────────────────

@pytest.mark.unit
def test_blank_control_threshold_is_declared_not_tuned():
    """阈值事先声明、不可调 —— 阈值一旦可调，就会被调到让当前这批通过。"""
    ok, detail = blank_control_ok(n_blank=10, n_blank_judged_negative=10)
    assert ok
    ok, _ = blank_control_ok(n_blank=10, n_blank_judged_negative=9)
    assert ok, "允许一格失手（10 格里 9 格）"
    ok, detail = blank_control_ok(n_blank=10, n_blank_judged_negative=8)
    assert not ok and "作废" in detail


@pytest.mark.unit
def test_blank_control_needs_enough_cells():
    ok, detail = blank_control_ok(n_blank=2, n_blank_judged_negative=2)
    assert not ok, "2 格空白对照证明不了什么"


# ── 判据从 CRITERIA.md 程序化抽取 ────────────────────────────────

@pytest.mark.unit
def test_criteria_section_is_extracted_verbatim():
    """判据段落从 CRITERIA.md 抽，不手抄 —— 手抄出过两次不可比。"""
    text = criteria_section(CRITERIA, "columns")
    assert "算柱" in text and "不算柱" in text
    assert "标高符号" in text, "已知的分歧点必须随判据一起带出去"
    assert "## walls" not in text, "只取本节，不串到下一节"


@pytest.mark.unit
def test_unknown_criteria_section_raises():
    """找不到判据就报错 —— 绝不能静默发出一份没有判据的批次。"""
    with pytest.raises(KeyError):
        criteria_section(CRITERIA, "no_such_class")


# ── 判据守卫：只有指针、没有判据的小节不能发出去 ─────────────────────

@pytest.mark.unit
def test_pointer_only_criteria_are_rejected(tmp_path):
    """只有指路、没有判据的小节不能发出去。

    曾经的 `equipment` 小节只有一行「见对应 json 的 note」、`slabs` 只有一句
    要点 —— 不拦的话，生成器会发出一份**没有判据**的批次，判读者自己猜标准。

    用合成文件测，不绑真实数据：真实小节会被补全（v10 已补），
    绑着它们，测试会在判据写好的那一刻反过来变红。
    """
    doc = tmp_path / "CRITERIA.md"
    doc.write_text("## gadgets —— 见对应 `gadgets_v1.json` 的 note\n\n"
                   "## widgets —— 什么算小部件\n\n一行要点。\n\n"
                   "## next —— 下一节\n", encoding="utf-8")
    for key in ("gadgets", "widgets"):
        with pytest.raises(KeyError, match="判据"):
            criteria_section(doc, key)


@pytest.mark.unit
@pytest.mark.parametrize("key", ["columns", "walls", "beams", "pipes", "equipment", "slabs"])
def test_substantive_criteria_still_extract(key):
    """有实质判据的小节照常抽取 —— 守卫不能误伤。"""
    assert len(criteria_section(CRITERIA, key).splitlines()) >= 8


# ── 线状构件（墙/梁/管）的标记 ─────────────────────────────────────

@pytest.mark.unit
def test_outline_element_marks_as_box():
    from core.model3d.gold.batch_design import element_mark

    mark = element_mark({"outline": [[0, 0], [2, 0], [2, 1], [0, 1]]}, to_page=lambda x, y: (x, y))
    assert mark.shape == "box"
    assert mark.bbox == (0, 0, 2, 1)


@pytest.mark.unit
def test_path_element_marks_as_line_with_covering_bbox():
    """墙/梁/管是两点 `path`：画红线，裁框取两端点的包络。"""
    from core.model3d.gold.batch_design import element_mark

    mark = element_mark({"path": [[1, 5], [9, 2]]}, to_page=lambda x, y: (x, y))
    assert mark.shape == "line"
    assert mark.line == ((1, 5), (9, 2))
    assert mark.bbox == (1, 2, 9, 5)


@pytest.mark.unit
@pytest.mark.parametrize("el", [{}, {"outline": [[0, 0], [1, 1]]}, {"path": [[0, 0]]}])
def test_degenerate_elements_have_no_mark(el):
    """点数不够的构件画不出标记 —— 返回 None，由调用方跳过，不假装画了。"""
    from core.model3d.gold.batch_design import element_mark

    assert element_mark(el, to_page=lambda x, y: (x, y)) is None


@pytest.mark.unit
def test_every_generator_kind_has_real_criteria():
    """生成器登记的每一类都必须抽得到实质判据 —— 挡住「先加类、判据以后再补」。"""
    from scripts.model3d.gold_batch import KIND_SPEC

    for kind in KIND_SPEC:
        criteria_section(CRITERIA, kind)          # 抽不到会抛 KeyError
    assert {"walls", "beams", "pipes"} <= set(KIND_SPEC), "线状三类要登记"
