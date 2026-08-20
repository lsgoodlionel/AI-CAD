"""强制性国家标准的条文义务等级。

《工程建设强制性国家标准》（GB 55001~55037 通用规范系列）
**全部条文必须严格执行** —— 这是标准类型决定的事实，
不该逐条从义务词去猜。

**实测**：GB 55023-2022《施工脚手架通用规范》导入后 75 条里
52 条被判成 `SHOULD` 且 `is_mandatory=f`，因为当时书名被污染成了
序言里的半句话，`is_mandatory_standard()` 认不出来。
标题守卫修好后强制标志会对，但还剩一个内部矛盾：
**强制为真、义务等级却仍是 SHOULD**——两个字段自相矛盾。
"""
import pytest


@pytest.mark.unit
def test_universal_standard_forces_mandatory_and_level():
    """通用规范的条文：强制标志与义务等级必须一起抬到强制。"""
    from services.regulation_importer import build_article_params

    params = build_article_params(
        "b1", {"article_no": "4.1.1", "raw_text": "脚手架应…",
               "obligation_level": "SHOULD", "is_mandatory": False},
        "GB 55023-2022《施工脚手架通用规范》")
    assert params["is_mandatory"] is True
    assert params["obligation_level"] == "MUST"


@pytest.mark.unit
def test_prohibition_level_is_not_downgraded_to_must():
    """`MUST_NOT`（严禁）比 `MUST` 更强——不能被抬成 `MUST` 反而弱化。"""
    from services.regulation_importer import build_article_params

    params = build_article_params(
        "b1", {"article_no": "4.1.2", "raw_text": "严禁…",
               "obligation_level": "MUST_NOT", "is_mandatory": True},
        "GB 55023-2022《施工脚手架通用规范》")
    assert params["obligation_level"] == "MUST_NOT"


@pytest.mark.unit
def test_non_universal_standard_keeps_its_own_level():
    """非通用规范不做任何抬升——推荐性标准里 SHOULD 就是 SHOULD。"""
    from services.regulation_importer import build_article_params

    params = build_article_params(
        "b1", {"article_no": "4.1.1", "raw_text": "宜…",
               "obligation_level": "SHOULD", "is_mandatory": False},
        "JGJ 130-2011《建筑施工扣件式钢管脚手架安全技术规范》")
    assert params["is_mandatory"] is False
    assert params["obligation_level"] == "SHOULD"


@pytest.mark.unit
def test_mandatory_flag_and_level_never_contradict():
    """强制为真时义务等级不得是 SHOULD/MAY —— 两个字段自相矛盾时，
    下游（审图报告、图谱过滤）会按不同字段得出不同结论。"""
    from services.regulation_importer import build_article_params

    for level in ("SHOULD", "MAY", "MUST", "MUST_NOT"):
        params = build_article_params(
            "b1", {"article_no": "1.1", "raw_text": "x",
                   "obligation_level": level, "is_mandatory": True}, None)
        assert params["obligation_level"] in ("MUST", "MUST_NOT"), level
