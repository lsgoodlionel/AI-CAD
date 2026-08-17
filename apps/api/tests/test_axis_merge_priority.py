"""识别轴号优先于档案轴号。

**为什么**:档案的 axis 条目是**未经校验的 OCR 原文**,实测样本
`IX | 80 | 3 | 1 | 0 | 4 | BY | M | E | P | S | A` ——
`IX` 含国标禁用字母 I(§8.0.4),`80/3/0` 是尺寸碎片,
`BY/M/E/P/S/A` 是图框专业代号。43643 条覆盖全部 2309 张图。

识别路径的轴号由几何推导 + 国标校验(全项目 0 违规)得来,质量高一个量级。

**旧行为的 bug**:`_merge_axes` 只在旧标签**为空**时升级,而档案噪声非空,
且档案先合、识别后合 —— 于是噪声恒定压过真轴号。
"""
from __future__ import annotations

import pytest

from services.model_elements import _merge_axes


@pytest.mark.unit
def test_recognised_label_overrides_archive_noise():
    archive = {"x": [["IX", 10.0]], "y": [["BY", 5.0]]}
    recognised = {"x": [["1", 10.0]], "y": [["A", 5.0]]}
    merged = _merge_axes(archive, recognised, authoritative=True)
    assert merged["x"] == [["1", 10.0]]
    assert merged["y"] == [["A", 5.0]]


@pytest.mark.unit
def test_archive_does_not_override_recognised():
    """反向不成立 —— 档案不是权威,不能覆盖识别结果。"""
    recognised = {"x": [["1", 10.0]], "y": []}
    archive = {"x": [["80", 10.0]], "y": []}
    merged = _merge_axes(recognised, archive)      # 默认非权威
    assert merged["x"] == [["1", 10.0]]


@pytest.mark.unit
def test_empty_label_still_upgraded_by_either_source():
    """无标签轴线仍应被任一来源升级 —— 这条旧行为要保住。"""
    agg = {"x": [["", 10.0]], "y": []}
    assert _merge_axes(agg, {"x": [["3", 10.0]], "y": []})["x"] == [["3", 10.0]]


@pytest.mark.unit
def test_authoritative_still_appends_new_axes():
    agg = {"x": [["1", 10.0]], "y": []}
    merged = _merge_axes(agg, {"x": [["2", 20.0]], "y": []}, authoritative=True)
    assert sorted(merged["x"], key=lambda e: e[1]) == [["1", 10.0], ["2", 20.0]]


@pytest.mark.unit
def test_authoritative_does_not_blank_out_an_existing_label():
    """权威来源给的是空标签时,不能把已有标签抹掉。"""
    agg = {"x": [["1", 10.0]], "y": []}
    merged = _merge_axes(agg, {"x": [["", 10.0]], "y": []}, authoritative=True)
    assert merged["x"] == [["1", 10.0]]
