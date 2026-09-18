"""合理性阈值表的取证纪律测试。

这些测试不验算任何工程结论，只守住一条规矩：**表里的每个数字都要有出处**。
本仓库有过凭记忆写国标常量的教训（平法代号表混进非平法代号、比例表里出现
国标根本没有的 1:75），而合理性分析会据这些数字否定构件 —— 一个记错的数字
会把真构件判成「不可能」，比不查更坏。
"""
from __future__ import annotations

import re

import pytest

from core.model3d.plausibility.codes import (
    EMPIRICAL,
    LIMITS,
    PHYSICS,
    UNVERIFIED,
    Limit,
    limit,
    unverified_keys,
    value,
)

pytestmark = pytest.mark.unit

#: 规范/图集出处的形状：编号（GB55008-2021、GB 55031-2022、22G101-1 …）+ 条款号。
#: 条款号既接受 `§4.4.4` 也接受裸的 `4.4.4`，但**必须有**，「GB55008」这种
#: 只报书名不报条款的写法查不回去，等于没有出处。
_CODE_SOURCE = re.compile(r"^[0-9A-Z][0-9A-Za-z一-鿿 .\-/]*\s(?:§\s*)?\d+(?:\.\d+)+")

#: UNVERIFIED 必须交代查过什么，冒号后至少这么多字才算交代清楚。
_MIN_UNVERIFIED_EXPLANATION = 10


def _code_sourced() -> list[Limit]:
    return [v for v in LIMITS.values() if _CODE_SOURCE.match(v.source)]


def test_每条限值的出处属于规范_经验_物理_未取证四类之一():
    # Arrange
    allowed_markers = (EMPIRICAL, PHYSICS)

    # Act
    misclassified = [
        (v.key, v.source)
        for v in LIMITS.values()
        if v.source not in allowed_markers
        and not v.source.startswith(UNVERIFIED)
        and not _CODE_SOURCE.match(v.source)
    ]

    # Assert
    assert misclassified == [], f"出处既不是规范号+条款号，也不是 EMPIRICAL/PHYSICS/UNVERIFIED：{misclassified}"


def test_键名与限值自带的key一致():
    # Arrange / Act
    mismatched = [(k, v.key) for k, v in LIMITS.items() if k != v.key]

    # Assert —— 键名错位会让 limit() 取到另一条限值的数字
    assert mismatched == []


def test_规范来源的限值必须带逐字原文摘录():
    # Arrange
    code_limits = _code_sourced()

    # Act
    without_text = [v.key for v in code_limits if not v.text.strip()]

    # Assert
    assert code_limits, "至少应有若干条落到规范原文，否则这张表没有取证意义"
    assert without_text == [], f"规范来源却没有原文摘录，无法回查：{without_text}"


def test_规范原文摘录里必须出现该限值的数字():
    """原文摘录要能直接佐证 value —— 摘一段不含这个数的话等于没摘。"""
    # Arrange
    def _numbers(val: object) -> list[float]:
        if isinstance(val, dict):
            return [float(x) for x in val.values()]
        if isinstance(val, (tuple, list)):
            return [float(x) for x in val]
        return [float(val)]

    # Act
    unsupported = []
    for lim in _code_sourced():
        for num in _numbers(lim.value):
            token = f"{num:g}"
            if token not in lim.text:
                unsupported.append((lim.key, token))

    # Assert
    assert unsupported == [], f"限值数字在原文摘录里找不到：{unsupported}"


def test_经验与物理来源的限值必须写明理由():
    # Arrange
    marker_limits = [v for v in LIMITS.values() if v.source in (EMPIRICAL, PHYSICS)]

    # Act
    without_note = [v.key for v in marker_limits if not v.note.strip()]
    with_text = [v.key for v in marker_limits if v.text.strip()]

    # Assert
    assert without_note == [], f"EMPIRICAL/PHYSICS 必须在 note 里写清凭什么：{without_note}"
    assert with_text == [], f"非规范来源不应伪装成有原文摘录：{with_text}"


def test_未取证的限值必须写明查过哪些书和关键词():
    # Arrange
    unverified = [v for v in LIMITS.values() if v.source.startswith(UNVERIFIED)]

    # Act
    too_terse = [
        v.key
        for v in unverified
        if len(v.source.partition(":")[2].strip()) < _MIN_UNVERIFIED_EXPLANATION
    ]

    # Assert —— 「UNVERIFIED:待取证」这种写法等于没查，下一个人还得从头查一遍
    assert too_terse == [], f"UNVERIFIED 没交代检索过程：{too_terse}"


def test_未取证的限值必须保留占位值而不能填估计数():
    """查不到就留空占位，编一个数才是最坏的结果。"""
    # Arrange
    unverified = [v for v in LIMITS.values() if v.source.startswith(UNVERIFIED)]

    # Act
    def _is_placeholder(val: object) -> bool:
        if isinstance(val, dict):
            return not val
        if isinstance(val, (tuple, list)):
            return all(float(x) == 0.0 for x in val)
        return float(val) == 0.0

    filled = [v.key for v in unverified if not _is_placeholder(v.value)]

    # Assert
    assert filled == [], f"未取证却填了数字：{filled}"


def test_limit对未登记的key抛KeyError():
    # Arrange
    unknown = "column.no_such_limit"

    # Act / Assert
    with pytest.raises(KeyError):
        limit(unknown)


def test_value返回与limit一致的取值():
    # Arrange
    key = "beam.min_width_mm"

    # Act
    got = value(key)

    # Assert
    assert got == limit(key).value == 200.0


def test_带UNVERIFIED的限值集合被显式登记为不可用于否定结论():
    """回归测试：锁住「UNVERIFIED 的限值不得用于 impossible/implausible」这条纪律。

    理由：这四条的原始出处（GB 50010 轴压比与 fc 表、GB 50017 容许长细比、
    GB 50010 板跨厚比）都不在本项目知识库内，数字无从核对。规则模块必须先问
    `unverified_keys()`，凡在这个集合里的 key 只能出 `suspect`，否则就会出现
    「用一个没出处的数字宣布真构件不可能」——正是本表要防的事。

    规则模块由别人写，这里不 import 它们；只把该集合本身钉死，
    未来谁要给这四条填数，必须连同出处一起填，这条测试才会自然放行。
    """
    # Arrange
    expected_unverified = {
        "column.max_axial_ratio",
        "column.max_slenderness",
        "slab.max_span_thickness_ratio",
        "concrete.fc_mpa_by_grade",
    }

    # Act
    actual = set(unverified_keys())

    # Assert
    assert actual == expected_unverified
    assert all(not LIMITS[k].verified for k in actual)
    assert all(LIMITS[k].verified for k in set(LIMITS) - actual)


def test_单位字段非空且限值不得混用米与毫米():
    # Arrange
    millimetre_keys = [k for k in LIMITS if k.endswith("_mm")]

    # Act
    missing_unit = [v.key for v in LIMITS.values() if not v.unit.strip()]
    wrong_unit = [k for k in millimetre_keys if LIMITS[k].unit != "mm"]

    # Assert
    assert missing_unit == []
    assert wrong_unit == [], f"键名写 _mm 单位却不是 mm：{wrong_unit}"
