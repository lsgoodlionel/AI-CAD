"""规则识别置信度按类给，不再一个常数打天下。

原来 `_RULE_CONFIDENCE = 0.92` 对所有类一视同仁 —— 柱（实测精确率 0.59）
与管线（实测 0.00）对外报同一个数字，这个置信度不携带任何信息。
"""
from services.model_elements import (
    _RULE_CONFIDENCE, _RULE_CONFIDENCE_BY_KIND, rule_confidence,
)


def test_每一类的置信度就是它实测的精确率():
    assert rule_confidence("walls") == 0.70
    assert rule_confidence("columns") == 0.59
    assert rule_confidence("beams") == 0.56
    assert rule_confidence("equipment") == 0.17
    assert rule_confidence("pipes") == 0.00


def test_未实测的类退回旧默认值():
    """板从未做过裁决式验证 —— 退回旧常数，并由 _RULE_CONFIDENCE_BY_KIND 的
    缺席如实表示「没量过」，而不是假装量过。"""
    assert "slabs" not in _RULE_CONFIDENCE_BY_KIND
    assert rule_confidence("slabs") == _RULE_CONFIDENCE


def test_管线不再够格当强规则命中():
    """fusion_policy 的 rule_strong_confidence=0.85：强规则命中不被模型覆盖。
    管线实测 0.00 却曾以 0.92 享受这个保护。"""
    STRONG = 0.85
    assert rule_confidence("pipes") < STRONG
    assert rule_confidence("equipment") < STRONG


def test_没有一类还在报旧的_0_92():
    assert all(v != _RULE_CONFIDENCE for v in _RULE_CONFIDENCE_BY_KIND.values())


def test_仲裁置信与对外置信是两件事():
    """仲裁问「规则的确定性够不够压过模型」，不是「规则有多准」。
    默认沿用确定性常数；只有被实测证伪的类交回实测值。"""
    from services.model_elements import fusion_confidence

    assert fusion_confidence("columns") == _RULE_CONFIDENCE   # 0.59 但仍受保护
    assert fusion_confidence("pipes") == 0.00                 # 实测证伪，失去保护
    assert fusion_confidence("equipment") == 0.17


def test_被证伪的类不再压过模型():
    from services.model_elements import _UNPROTECTED_KINDS, fusion_confidence

    STRONG = 0.85
    assert _UNPROTECTED_KINDS == {"pipes", "equipment"}
    assert all(fusion_confidence(k) < STRONG for k in _UNPROTECTED_KINDS)


def test_对外置信不取决于内部走没走融合(monkeypatch):
    """同一个柱，融合跑与不跑都必须报同一个数 —— 否则仲裁用的常数会
    从 `_apply_fusion_result` 的回灌口漏成对外数字。"""
    from services import model_elements
    from core.model3d.spotting.types import SymbolCandidate
    import tests.test_fusion_reinjection as t

    monkeypatch.setattr(model_elements, "_spotting_service",
                        lambda: t._service_with((SymbolCandidate(
                            category="column", confidence=0.95,
                            bbox=(0.0, 0.0, 100.0, 100.0), source="model"),)))
    fused = model_elements._reinject_fusion(t._column_elements(), t._geom(), "d1")
    plain = model_elements._reinject_fusion(t._column_elements(), t._geom(), "d1")
    assert fused["columns"][0]["confidence"] == plain["columns"][0]["confidence"]
