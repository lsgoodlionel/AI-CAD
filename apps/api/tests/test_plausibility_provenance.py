"""跨两张出处表的纪律：**最强的结论要有最硬的依据**。

合理性引擎有两张出处表，各管一半：
- `codes.py`：规范限值（条款号 + 原文），查不到标 `UNVERIFIED`；
- `formulas.py`：数学/物理公式（书 + PDF 页），查不到标 `UNCITED`。

本文件不测某一条规则算得对不对（那是各族自己的用例），只测**这套纪律
有没有被绕过**：每条结论都有依据、`impossible` 档不靠没出处的东西立论、
两张表里「查不到」的条目没有被偷偷填上一个数。

为什么单开一个文件：这些断言横跨五个规则模块与两张表，塞进任何一个模块的
用例里都会让「谁该维护它」变得含糊。
"""
from __future__ import annotations

import pytest

from core.model3d.plausibility import codes, formulas, registry


@pytest.fixture(scope="module")
def rules():
    return registry.load_rules()


@pytest.mark.unit
def test_every_registered_rule_declares_a_basis(rules):
    """规则级依据不能为空 —— 结论级的空依据由 `Finding` 自己抛异常拦住，
    规则级没有这道拦，只能靠测试。"""
    missing = [r.id for r in rules if not (r.basis or "").strip()]
    assert missing == [], f"这些规则没写依据：{missing}"


@pytest.mark.unit
def test_rules_are_registered_under_all_five_families(rules):
    """五族都要在册。少一族不会报错，只会让那一类问题**从此查不出来**。"""
    prefixes = {rule.id.split(".", 1)[0] for rule in rules}
    assert {"geom", "dim", "support", "statics", "qty"} <= prefixes


@pytest.mark.unit
def test_unverified_limits_keep_their_placeholder_values():
    """没取证的限值必须保留占位，不能被人顺手填一个「差不多」的数。

    填进去的那一刻，`Limit.verified` 仍是 False，而规则会拿它去比 ——
    一个没有出处的数字冒充成了判据。
    """
    for key in codes.unverified_keys():
        limit = codes.limit(key)
        assert limit.value in ({}, 0.0, (0.0, 0.0)), \
            f"{key} 标着未取证却填了值 {limit.value!r}"
        assert ":" in limit.source, f"{key} 没写清查过什么"


@pytest.mark.unit
def test_uncited_formulas_declare_what_was_searched():
    for key in formulas.uncited_keys():
        source = formulas.formula(key).source
        assert source.startswith(formulas.UNCITED)
        assert len(source.split(":", 1)[1].strip()) >= 8, \
            f"{key} 没写清查过哪几本、什么关键词"


#: 规则级依据里回指公式的写法（`rules_quantity.formula_ref`）。
#: 规则对象在 import 时构造，那时渲染出处就成了快照，取证后来填的原文
#: 永远到不了 —— 所以规则级只写 key，渲染留到 `check` 里。
_REF_PREFIX = "见 formulas："


def _referenced_keys(basis: str) -> set[str]:
    if _REF_PREFIX not in basis:
        return set()
    tail = basis.split(_REF_PREFIX, 1)[1]
    # 指针后面可能还跟着别的说明，取到第一个句读为止
    for stop in ("）", ")", "。", "；", "\n"):
        tail = tail.split(stop, 1)[0]
    return {k.strip() for k in tail.split("、") if k.strip()}


@pytest.mark.unit
def test_rule_level_basis_points_at_keys_not_rendered_citations(rules):
    """规则级依据要回指 key，而不是渲染好的出处字符串。

    渲染 = 快照。取证任务把 `UNCITED` 换成真出处后，快照不会跟着变，
    界面上就会长期显示一条**已经过时的依据**，而且没人会发现。
    """
    referenced = {k for rule in rules for k in _referenced_keys(rule.basis)}
    assert referenced, "没有一条规则回指公式表 —— 出处那一层等于没接"
    unknown = referenced - set(formulas.FORMULAS)
    assert unknown == set(), f"依据里回指了不存在的公式 key：{unknown}"


@pytest.mark.unit
def test_uncited_formulas_force_the_impossible_tier_down(rules):
    """回指了未取证公式的规则，**出口**不得是 `impossible`。

    这里查的是机制而不是声明：`Rule.severity` 是规则的意图（import 期固定，
    不能随取证变），真正落到结论上的是运行时的 `formula_severity`。
    所以断言「同一组 key 过一遍降级函数，结果不再是 impossible」——
    取证补上之后它会自动升回去，不用改代码，也不用改这条测试。
    """
    from core.model3d.plausibility.rules_quantity import formula_severity

    uncited = set(formulas.uncited_keys())
    leaks = []
    for rule in rules:
        if rule.severity != "impossible":
            continue
        keys = _referenced_keys(rule.basis)
        if not keys & uncited:
            continue
        if formula_severity("impossible", *keys) == "impossible":
            leaks.append((rule.id, sorted(keys & uncited)))
    assert leaks == [], f"这些规则回指未取证公式却仍能出 impossible：{leaks}"


@pytest.mark.unit
def test_a_fully_cited_rule_keeps_its_impossible_tier():
    """反向：出处齐全时不该被无故降档 —— 否则这道闸就成了「一律降级」。"""
    from core.model3d.plausibility.rules_quantity import formula_severity

    cited = [k for k in formulas.FORMULAS if formulas.formula(k).cited]
    assert cited, "公式表里一条有出处的都没有 —— 取证那条线没落地"
    assert formula_severity("impossible", cited[0]) == "impossible"


@pytest.mark.unit
def test_formula_used_in_points_at_something_real():
    """`used_in` 是这张表与规则之间唯一的索引。指向不存在的东西，
    等于让「这条公式还有没有人用」永远查不清。"""
    known = {rule.id for rule in registry.all_rules()}
    for key, item in formulas.FORMULAS.items():
        for target in item.used_in:
            if "." not in target:
                continue
            is_rule = target in known
            is_module = target.startswith(("core.", "services."))
            assert is_rule or is_module, f"{key}.used_in 指向了 {target}"
