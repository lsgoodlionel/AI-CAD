"""OCR 像素→点的换算必须用**实际渲染 DPI**，不是标称 DPI。

实测触发：档案里 `category='elevation'` 的 bbox，在**确实有文本层**的页面上
**90% 落在空白处**（199 条可判定里 179 条）；room_name 96%、axis 95%、
dimension 98% 同样落空。用 PyMuPDF 反查同一段文字，最近的一处也在
431~589 pt 之外，且偏移倍数**每张图不同**（1.50 / 1.57 / 1.63 / 1.84）。

根因：`_render_first_page` 对大图**自适应降 DPI** 后按 `eff_dpi` 渲染，
而 `run_ocr` 换算坐标时用的是**标称 dpi** —— 工程图多是 A0/A1，
几乎每张都触发降采样，于是全部坐标按各自的 `dpi/eff_dpi` 倍偏小。

后果：所有按位置消费档案的通道都在错的坐标上工作 ——
「档案 OCR 文字 → 构件类型标签（就近关联）」实测只产出 **31 条 = 0.005%**，
与此完全一致。
"""
import pytest

from core.model3d.ocr import service as ocr_service


def test_小图不降采样时换算比例就是标称dpi():
    # 最长边 1000pt × 200dpi / 72 ≈ 2778px，小于上限，不触发降采样
    eff = ocr_service.effective_dpi(page_longest_pt=1000.0, dpi=200)
    assert eff == 200


def test_大图降采样后换算必须用降过的dpi():
    """A1 图长边约 2384pt，200dpi 下约 6622px，超过上限必被降。"""
    eff = ocr_service.effective_dpi(page_longest_pt=2384.0, dpi=200)
    assert eff < 200
    # 降采样后长边正好落在上限
    assert 2384.0 * eff / 72.0 == pytest.approx(ocr_service._MAX_RENDER_PX, rel=1e-6)


def test_换算比例随图幅变化而不是固定():
    """偏移倍数每张图不同，正是因为 eff_dpi 依赖图幅 —— 用标称 dpi 就错。"""
    a = ocr_service.effective_dpi(page_longest_pt=2384.0, dpi=200)   # A1
    b = ocr_service.effective_dpi(page_longest_pt=3370.0, dpi=200)   # A0
    assert a != b


def test_零或负图幅安全降级():
    assert ocr_service.effective_dpi(page_longest_pt=0.0, dpi=200) == 200
    assert ocr_service.effective_dpi(page_longest_pt=-5.0, dpi=200) == 200
