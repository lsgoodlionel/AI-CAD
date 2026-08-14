"""圆检测的两道闸 —— 实测把整个建模卡死了一次。

**实测事故**(上海大歌剧院,模型 v51 重建):recognize 阶段停在「4层」
**13 分钟零进展**,而 worker CPU 稳定 200%、内存不涨。py-spy 抓栈:

```
Thread 298 (active): "ThreadPoolExecutor-2_0"
    detect_circles_px (core/model3d/circle_detector.py:66)
Thread 337 (active): "ThreadPoolExecutor-2_1"
    detect_circles_px (core/model3d/circle_detector.py:66)
```

两个线程都卡在 HoughCircles,**而且都是已被 20 秒超时「放弃」的僵尸**——
`asyncio.wait_for` 取消得了 await，取消不了 executor 里正在跑的同步函数。
线程池 `max_workers=2` 被占满后，后续每张图都在等一个**永不空出**的池。
**这不是慢，是死锁。**

两道闸:

1. **像素预算**:`_MAX_RENDER_PX` 限的是最长边，8000×6000 仍有 4800 万像素，
   HoughCircles 在这个规模上要跑几分钟。改按**总像素**限制。
2. **原点缺向**:`_origin_pt` 现在会返回 `None`（区分「原点在 0」与「没找到」），
   而 `circle_px_to_meter` 直接拿它做减法 → TypeError → 被外层 `except`
   吞掉 → **整张图的桩静默全丢**。这是 E3-B「+2705 桩」的存亡问题。
"""
from __future__ import annotations

import pytest

from core.model3d.circle_detector import (
    MAX_RENDER_MEGAPIXELS, circle_px_to_meter, render_scale_for,
)


# ── 闸一:像素预算 ────────────────────────────────────────────────

@pytest.mark.unit
def test_huge_pages_are_scaled_within_the_pixel_budget():
    """**核心用例**：超大图必须降到预算内，否则 HoughCircles 跑不完。"""
    scale = render_scale_for(width_px=8000, height_px=6000)
    megapixels = 8000 * scale * 6000 * scale / 1e6
    assert megapixels <= MAX_RENDER_MEGAPIXELS * 1.01


@pytest.mark.unit
def test_normal_pages_are_not_scaled_down():
    """**不能误伤正常图** —— 降采样会削弱桩的检出（E3-B +2705 靠全分辨率）。"""
    assert render_scale_for(width_px=1400, height_px=1000) == 1.0


@pytest.mark.unit
def test_budget_keeps_single_image_within_the_recognition_timeout():
    """**预算的判据是能否在超时内跑完**，不是理论上能否分辨最小目标。

    我先按「0.5 米桩在 8 MP 下仍有 5px 半径」推算过 8 MP 够用 ——
    分辨率的账算对了，**耗时的账完全没算**。在那张图上逐档实测：

    | 预算 | 耗时 | 检出 |
    |---|---:|---:|
    | 8 MP | 76.1 s | 137 |
    | 4 MP | 21.9 s | 134 |
    | 2 MP | **5.7 s** | 129 |

    76 秒的结果会被 `_RECOGNIZE_TIMEOUT_SEC=20` 丢弃 ⇒ **实际检出 0**。
    所以预算必须让单图在超时内返回，否则算得再准也拿不到。
    """
    from services.model_elements import _RECOGNIZE_TIMEOUT_SEC

    # 实测 2 MP 上该图 5.7 秒；留足余量应对更密的图
    assert MAX_RENDER_MEGAPIXELS <= 4.0, "超过 4 MP 实测就逼近/超出超时"
    assert _RECOGNIZE_TIMEOUT_SEC >= 20


@pytest.mark.unit
def test_scale_is_never_zero_or_negative():
    """再离谱的输入也要给出可用的比例，不能除零。"""
    for w, h in ((100000, 100000), (1, 1), (0, 0)):
        assert render_scale_for(width_px=w, height_px=h) > 0


@pytest.mark.unit
def test_budget_is_bounded_by_hough_cost_not_by_longest_edge():
    """**判据是总像素,不是最长边** —— 长条图与方图的耗时天差地别。

    8000×500（400 万像素）该原样跑，而 8000×6000（4800 万）必须降。
    旧判据只看最长边，两者都放行。
    """
    assert render_scale_for(width_px=3000, height_px=400) == 1.0
    assert render_scale_for(width_px=8000, height_px=6000) < 1.0


# ── 闸二:原点缺向不得让整图静默归零 ───────────────────────────────

@pytest.mark.unit
def test_missing_origin_falls_back_to_zero():
    """**回归用例**：`_origin_pt` 可能返回 None，减法会 TypeError。

    被外层 `except` 吞掉后整张图的桩全丢，且日志里看不出所以然。
    与 `transform_from_geometry` 一致：按 0 兜底，不拒绝。
    """
    got = circle_px_to_meter(100.0, 100.0, 10.0, dpi=150, page_h_pt=1000.0,
                             scale_m_pt=0.05, origin_pt=(None, None))
    assert all(isinstance(v, float) for v in got)


@pytest.mark.unit
def test_partial_origin_is_handled_per_direction():
    """缺哪个方向就兜底哪个 —— 实测缺的都是**一个**方向。"""
    got = circle_px_to_meter(100.0, 100.0, 10.0, dpi=150, page_h_pt=1000.0,
                             scale_m_pt=0.05, origin_pt=(None, 50.0))
    assert all(isinstance(v, float) for v in got)


@pytest.mark.unit
def test_present_origin_still_applies():
    """有原点时照常平移 —— 兜底不能把正常路径改掉。"""
    with_origin = circle_px_to_meter(100.0, 100.0, 10.0, dpi=150,
                                     page_h_pt=1000.0, scale_m_pt=0.05,
                                     origin_pt=(20.0, 0.0))
    without = circle_px_to_meter(100.0, 100.0, 10.0, dpi=150,
                                 page_h_pt=1000.0, scale_m_pt=0.05,
                                 origin_pt=(0.0, 0.0))
    assert with_origin[0] < without[0]
