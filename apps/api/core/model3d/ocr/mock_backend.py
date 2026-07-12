"""离线 mock OCR 后端（无 paddle/GPU 时的确定性桩）。

从可选的「预置文本框」合成识别结果，使「渲染 → OCR → 分类 → 消费」整链路在
CI（无重依赖）下可端到端跑通、可测试。这是 mock 而非真实识别：文本来自入参
预置，不做任何图像推理。

真实链路用 PaddleOcrBackend；本后端仅供离线/测试。
"""
from __future__ import annotations


class MockOcrBackend:
    """确定性 mock：识别结果 = 构造时注入的 seed 文本框。

    seed: list[(text, bbox_pixels, confidence)]。
    """

    name = "mock"

    def __init__(self, seed=None):
        self._seed = list(seed or [])

    def is_available(self) -> bool:
        return True

    def recognize(
        self, image_rgb, warnings: list[str]
    ) -> list[tuple[str, tuple[float, float, float, float], float]]:
        if not self._seed:
            warnings.append("mock OCR 无预置文本（离线占位，非真实识别）")
        return list(self._seed)
