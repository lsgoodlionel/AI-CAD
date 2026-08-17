"""轴距异常只**标记**，不自动修比例（上一版改坏了，已回退）。

**为什么不能反推比例**：轴距是 `pt差 × 比例` 算出来的，异常时有两种可能，
而**从数据上无法区分**：

| 可能 | 说明 |
|---|---|
| 比例错了 | 乘个系数能修好 |
| **轴线检测噪声** | 把两条紧邻的线当成轴网 —— 乘系数只会更离谱 |

实测清单证明后者是主因：修正倍数最大的几张，原轴距是
**0.11 / 0.12 / 0.28 米** —— 11 厘米在图上就是两条挨着的线，
不可能是柱网；另一端 177.91 米的「单跨」同样不可能。
上一版按轴距反推，把 F6 层跨度从 89 米压成 **5 米**。

所以：**判不出就说判不出**，把证据摆给人，让人分辨是哪一种。
"""
from __future__ import annotations

import pytest

from services.axis_gap_anomaly import (
    GAP_SANE_RANGE_M, detect_gap_anomaly, summarize_gap_anomalies,
)


@pytest.mark.unit
def test_normal_gap_is_not_flagged():
    """接近共识就不报 —— 免得人被无效告警淹没。"""
    assert detect_gap_anomaly("d1", gap_m=8.4, consensus_m=8.0, samples=20) is None


@pytest.mark.unit
def test_tiny_gap_is_flagged_as_noise():
    """**0.11 米不可能是柱网** —— 判为轴线检测噪声，不是比例问题。"""
    got = detect_gap_anomaly("d1", gap_m=0.11, consensus_m=8.0, samples=120)
    assert got is not None
    assert "噪声" in got["likely_cause"]


@pytest.mark.unit
def test_huge_gap_is_flagged_too():
    """177 米的「单跨」同样不可能。"""
    got = detect_gap_anomaly("d1", gap_m=177.9, consensus_m=8.0, samples=18)
    assert got is not None


@pytest.mark.unit
def test_moderate_deviation_says_scale_may_be_wrong():
    """轴距仍在工程合理区间、只是偏离共识 ⇒ **比例可疑**，与噪声区分开。

    2.65 米是紧凑柱网的下限附近，可能真实、也可能比例小了 3 倍 ——
    这种才值得人看一眼。
    """
    got = detect_gap_anomaly("d1", gap_m=2.65, consensus_m=8.0, samples=55)
    assert got is not None
    assert "比例" in got["likely_cause"]
    assert "噪声" not in got["likely_cause"]


@pytest.mark.unit
def test_evidence_is_carried():
    """**要给证据**，人才分得清是哪一种。"""
    got = detect_gap_anomaly("d1", gap_m=0.11, consensus_m=8.0, samples=120)
    assert got["gap_m"] == pytest.approx(0.11)
    assert got["samples"] == 120
    assert got["consensus_m"] == pytest.approx(8.0)


@pytest.mark.unit
def test_no_correction_is_offered():
    """**不给「建议比例」** —— 反推不成立，给了就是诱导人接受错值。"""
    got = detect_gap_anomaly("d1", gap_m=0.11, consensus_m=8.0, samples=120)
    assert "suggested_scale" not in got


@pytest.mark.unit
def test_missing_inputs_are_safe():
    assert detect_gap_anomaly("d1", None, 8.0, 10) is None
    assert detect_gap_anomaly("d1", 8.0, None, 10) is None
    assert detect_gap_anomaly("d1", 0.0, 8.0, 10) is None


@pytest.mark.unit
def test_sane_range_covers_real_column_grids():
    """合理区间要容得下真实柱网（常见 6~12 米，紧凑的 3 米也有）。"""
    low, high = GAP_SANE_RANGE_M
    assert low <= 3.0
    assert high >= 12.0


@pytest.mark.unit
def test_summary_groups_by_cause():
    got = summarize_gap_anomalies([
        detect_gap_anomaly("a", 0.11, 8.0, 120),
        detect_gap_anomaly("b", 2.65, 8.0, 55),
        None,
    ])
    assert got["count"] == 2
    assert got["by_cause"]


@pytest.mark.unit
def test_summary_of_nothing_is_safe():
    assert summarize_gap_anomalies([])["count"] == 0
