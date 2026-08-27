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
