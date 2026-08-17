"""不按比例图纸识别单测(N.T.S / 文字类图纸不应有坐标变换)。"""
from services.non_scaled_drawings import (
    has_nts_marker,
    is_non_scaled,
    is_non_scaled_title,
)


def test_detect_nts_variants():
    assert has_nts_marker(["比例 N.T.S"]) is True
    assert has_nts_marker(["NTS"]) is True
    assert has_nts_marker(["n.t.s."]) is True
    assert has_nts_marker(["本图不按比例"]) is True
    assert has_nts_marker(["比例 1:100"]) is False


def test_detect_text_only_titles():
    """用户实例:图纸目录、系统原理图。"""
    assert is_non_scaled_title("建筑-竣工图--图纸目录") is True
    assert is_non_scaled_title("给排水-竣工图--雨水系统原理图") is True
    assert is_non_scaled_title("建筑-竣工图--外围护建筑做法表1") is True
    # 「构造做法图」是详图(按比例绘制),不得误判为无比例
    assert is_non_scaled_title("建筑-竣工图--隔声隔振构造做法图") is False
    assert is_non_scaled_title("结构-竣工图--一层柱平面图") is False


def test_is_non_scaled_returns_reason():
    ok, reason = is_non_scaled("建筑-竣工图--图纸目录")
    assert ok is True and "目录" in reason
    ok2, reason2 = is_non_scaled("给排水-雨水系统图", ["比例 N.T.S"])
    assert ok2 is True                       # 标题命中「系统图」


def test_nts_by_text_when_title_normal():
    ok, reason = is_non_scaled("给排水-竣工图--雨水大样", ["N.T.S"])
    assert ok is True and "N.T.S" in reason


def test_normal_plan_is_scaled():
    ok, _ = is_non_scaled("结构-竣工图--地下一层梁平面图", ["比例 1:150"])
    assert ok is False
