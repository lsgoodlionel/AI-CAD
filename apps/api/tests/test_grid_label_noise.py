"""轴号噪声过滤 —— §8.0.4 的双字母有固定形式。

**实测问题**:模型 v32 的 F2/F4 层,x 向轴号序列开头是 `BY`:

```
F2:  BY 1 2 3 4 5 6 7 8 9 10 11
F4:  BY 1 2 3 4 5 6 7 8 9 10
```

`BY` 是**图框专业代号**(Phase I 已查明:OCR 在图框读出的短标签是
`A/BY/E/M/P/S`),不是轴号。它能混进来,是因为
`_is_grid_label` 无条件放行「1~2 位字母」。

**国标依据**:GB/T 50001 §8.0.4「I、O、Z 不得用作轴线编号;
不够用时可用**双字母**或**字母加数字注脚**」。

「双字母」指 `AA`/`BB` 这类**重复同一字母**,「字母加数字注脚」指 `A1`/`B1`。
`BY` 是两个**不同**字母的组合,两种形式都不是。

而且双字母只在单字母不够用时才出现——单字母有 A~Y 跳过 I/O/Z 共 23 个,
一个分区有 23 条以上字母轴线才谈得上。
"""
from __future__ import annotations

import pytest

from services.model_elements import _is_grid_label


@pytest.mark.unit
@pytest.mark.parametrize("label", ["1", "2", "24", "A", "B", "Y", "Q"])
def test_plain_axis_labels_pass(label):
    assert _is_grid_label(label) is True


@pytest.mark.unit
@pytest.mark.parametrize("label", ["I", "O", "Z"])
def test_forbidden_letters_are_rejected(label):
    """§8.0.4:I、O、Z 不得用作轴线编号。"""
    assert _is_grid_label(label) is False


@pytest.mark.unit
@pytest.mark.parametrize("label", ["AA", "BB", "YY"])
def test_doubled_letters_pass(label):
    """§8.0.4「双字母」= 重复同一字母。"""
    assert _is_grid_label(label) is True


@pytest.mark.unit
@pytest.mark.parametrize("label", ["A1", "B2", "K1"])
def test_letter_with_numeric_suffix_passes(label):
    """§8.0.4「字母加数字注脚」。"""
    assert _is_grid_label(label) is True


@pytest.mark.unit
@pytest.mark.parametrize("label,why", [
    ("BY", "实测混进 F2/F4 的图框专业代号"),
    ("AC", "两个不同字母,既非双字母也非数字注脚"),
    ("PS", "图框代号"),
    ("EM", "图框代号"),
])
def test_mixed_two_letter_codes_are_rejected(label, why):
    """**核心用例**:两个不同字母的组合不是国标轴号形式。"""
    assert _is_grid_label(label) is False, why


@pytest.mark.unit
@pytest.mark.parametrize("label", ["1/A", "2/K"])
def test_fraction_form_passes(label):
    """§8.0.6 附加轴线分数式。"""
    assert _is_grid_label(label) is True


@pytest.mark.unit
@pytest.mark.parametrize("label", ["", "   ", "说明", "1234567"])
def test_junk_is_rejected(label):
    assert _is_grid_label(label) is False


@pytest.mark.unit
def test_zone_prefixed_labels_pass():
    """§8.0.5 分区编号「分区号-轴线号」。"""
    assert _is_grid_label("1-1") is True
    assert _is_grid_label("2-A") is True
