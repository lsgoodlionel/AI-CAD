"""全套金标准的评分口径——每种真值方法对错的定义不同。"""
import pytest

from core.model3d.gold.schema import parse_unit
from core.model3d.gold.score import score_class


def _cls(body):
    return parse_unit({"unit": "U", "source": {},
                       "classes": {"columns": body}}).classes["columns"]


@pytest.mark.unit
def test_count_scoring_reports_absolute_and_relative_error():
    truth = _cls({"method": "count", "count": 8, "confidence": 0.9,
                  "verified_by": ["human"]})
    s = score_class(truth, {"count": 10})
    assert s.abs_error == 2
    assert s.rel_error == pytest.approx(0.25)
    assert not s.exact


@pytest.mark.unit
def test_count_of_zero_does_not_divide_by_zero():
    """真值为 0 的块（挑空区）不能把相对误差算成无穷。"""
    truth = _cls({"method": "count", "count": 0, "confidence": 0.9,
                  "verified_by": ["human"]})
    s = score_class(truth, {"count": 4})
    assert s.abs_error == 4
    assert s.rel_error == pytest.approx(4.0)      # 以 1 为分母兜底


@pytest.mark.unit
def test_instance_scoring_is_by_axis_identity():
    """实体级按轴号配对，给出精确率/召回率。"""
    truth = _cls({"method": "instances", "confidence": 0.9,
                  "verified_by": ["human"],
                  "instances": [{"id": "1×A"}, {"id": "1×B"}, {"id": "2×A"}]})
    s = score_class(truth, {"instances": [{"id": "1×A"}, {"id": "2×A"},
                                          {"id": "9×Z"}]})
    assert s.matched == 2
    assert s.missed == ["1×B"]
    assert s.spurious == ["9×Z"]
    assert s.recall == pytest.approx(2 / 3)
    assert s.precision == pytest.approx(2 / 3)


@pytest.mark.unit
def test_size_error_only_counted_on_matched_instances():
    """尺寸误差只在配上对的实体上算——没配上的谈尺寸没意义。"""
    truth = _cls({"method": "instances", "confidence": 0.9,
                  "verified_by": ["human"],
                  "instances": [{"id": "1×A", "size_mm": [600, 600]},
                                {"id": "1×B", "size_mm": [600, 600]}]})
    s = score_class(truth, {"instances": [{"id": "1×A", "size_mm": [640, 600]}]})
    assert s.matched == 1
    assert s.size_error_mm == pytest.approx(40.0)   # 只看 1×A


@pytest.mark.unit
def test_text_scoring_ignores_whitespace_and_fullwidth_noise():
    """图名比对不该因空格/全角括号判错。"""
    truth = _cls({"method": "text", "text": "一层结构平面图（四）",
                  "confidence": 1.0, "verified_by": ["human"]})
    assert score_class(truth, {"text": " 一层结构平面图(四) "}).exact


@pytest.mark.unit
def test_field_scoring_reports_per_field_hits():
    truth = _cls({"method": "fields", "confidence": 1.0,
                  "verified_by": ["human"],
                  "fields": {"scale": "1:100", "drawing_no": "S-01",
                             "designer": "张三"}})
    s = score_class(truth, {"fields": {"scale": "1:100", "drawing_no": "S-02"}})
    assert s.matched == 1
    assert s.missed == ["designer"]
    assert s.wrong == ["drawing_no"]


@pytest.mark.unit
def test_excluded_truth_is_refused_by_the_scorer():
    """排除项不该被算分——真要算，说明调用方用错了。"""
    truth = _cls({"method": "count", "count": 4, "confidence": 0.9,
                  "verified_by": ["human"], "excluded": "埋件图"})
    with pytest.raises(ValueError, match="不计分"):
        score_class(truth, {"count": 4})


# --- 分区序号是任意的：评分前先按内容对齐 -----------------------------

@pytest.mark.unit
def test_zones_are_aligned_by_content_not_by_number():
    """两边的分区编号不同不算错——先按内容把分区对上再比。

    **实测**：给「首层框架梁平面整体配筋图」修掉索引符号误判后，
    一个分区被正确删除，其后分区**整体前移**（真值的 `3·A~E`
    对上了识别的 `2·A~E`），F1 从 78% 假跌到 68.6%。

    `zone_index` 是序号不是语义，增删一个分区，后面全错位。
    分区的真实身份是 §8.0.5 的分区号，而它需人工确认；未确认时
    评分器应当**按标签重合度把分区配对**，而不是假定编号相同。
    """
    truth = _cls({"method": "instances", "confidence": 1.0,
                  "verified_by": ["human"],
                  "instances": [{"id": "A", "zone": "3"}, {"id": "B", "zone": "3"},
                                {"id": "1", "zone": "0"}]})
    got = {"instances": [{"id": "A", "zone": "2"}, {"id": "B", "zone": "2"},
                         {"id": "1", "zone": "0"}]}
    s = score_class(truth, got)
    assert s.matched == 3
    assert s.exact


@pytest.mark.unit
def test_alignment_does_not_merge_two_distinct_zones():
    """对齐不能把两个真分区揉成一个——各自配各自的。"""
    truth = _cls({"method": "instances", "confidence": 1.0,
                  "verified_by": ["human"],
                  "instances": [{"id": "1", "zone": "1"}, {"id": "2", "zone": "1"},
                                {"id": "1", "zone": "2"}, {"id": "2", "zone": "2"}]})
    got = {"instances": [{"id": "1", "zone": "7"}, {"id": "2", "zone": "7"},
                         {"id": "1", "zone": "9"}, {"id": "2", "zone": "9"}]}
    assert score_class(truth, got).matched == 4


@pytest.mark.unit
def test_a_genuinely_missing_zone_still_shows_as_missing():
    """整个分区没识别出来，对齐不能把它变没。"""
    truth = _cls({"method": "instances", "confidence": 1.0,
                  "verified_by": ["human"],
                  "instances": [{"id": "A", "zone": "3"}, {"id": "1", "zone": "0"}]})
    s = score_class(truth, {"instances": [{"id": "1", "zone": "0"}]})
    assert s.matched == 1
    assert len(s.missed) == 1


# --- 标签配上了，不等于贴在正确的轴线上 -------------------------------

def _axes_truth(spacings):
    """按轴距链造真值：labels 1..n，相邻间距取自 spacings（毫米）。"""
    inst = [{"id": str(i + 1), "kind": "vertical",
             "to_next_mm": spacings[i] if i < len(spacings) else None}
            for i in range(len(spacings) + 1)]
    return parse_unit({"unit": "U", "source": {}, "classes": {"axes": {
        "method": "instances", "instances": inst,
        "confidence": 1.0, "verified_by": ["human"]}}}).classes["axes"]


@pytest.mark.unit
def test_a_shifted_label_run_is_caught_by_the_spacing_chain():
    """漏检一条轴线后，其后编号整体偏移——**集合比对看不出来**。

    **实测**（metro 首层框架梁配筋图）：⑦ 的圈没检出，识别器按顺序把
    余下 12 个圈标成 1~12，而它们的物理位置是 ①②③④⑤⑥**⑧⑨⑩⑫⑬⑭**。
    只比标签集合会报「12 个全配上」，其中 6 个贴错了轴线。

    轴距链是图纸自带的判据：真值每档 9300，而识别侧「6→7」的实测间距
    是相邻档的两倍 —— 那里少了一条轴线。
    """
    truth = _axes_truth([9300] * 6)                 # 7 条轴线，等距
    got = {"instances": [
        {"id": "1", "offset_pt": 0.0}, {"id": "2", "offset_pt": 155.0},
        {"id": "3", "offset_pt": 310.0}, {"id": "4", "offset_pt": 465.0},
        {"id": "5", "offset_pt": 620.0}, {"id": "6", "offset_pt": 775.0},
        {"id": "7", "offset_pt": 1085.0},           # ← 跳了一档（少了一条）
    ]}
    s = score_class(truth, got)
    assert s.matched == 7                            # 标签集合全中
    assert s.spacing_conflicts == ["6"]              # 但 6→7 这一档对不上
    assert not s.sequence_ok


@pytest.mark.unit
def test_a_correct_sequence_reports_no_conflict():
    """位置与标签自洽时不报冲突（比例不同不算错——只看相对关系）。"""
    truth = _axes_truth([9300, 9300, 7500])
    got = {"instances": [
        {"id": "1", "offset_pt": 0.0}, {"id": "2", "offset_pt": 93.0},
        {"id": "3", "offset_pt": 186.0}, {"id": "4", "offset_pt": 261.0}]}
    s = score_class(truth, got)
    assert s.sequence_ok
    assert s.spacing_conflicts == []


@pytest.mark.unit
def test_without_offsets_the_check_is_skipped_not_failed():
    """识别侧没给位置时**跳过**这项检查，不能算作不合格。"""
    truth = _axes_truth([9300, 9300])
    s = score_class(truth, {"instances": [{"id": "1"}, {"id": "2"}, {"id": "3"}]})
    assert s.sequence_ok
    assert s.spacing_conflicts == []
