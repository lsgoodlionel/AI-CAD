"""图纸全文 OCR（model3d 域）。

CAD 导出 PDF 的正文标注（标高、轴号、构件名、尺寸、说明）多为**矢量绘制字形**，
``page.get_text`` 只能拿到标题栏文本。本模块把图纸高 DPI 栅格化后跑 OCR，产出
**带坐标 + 置信度 + 轻量语义分类**的结构化文本 token，供下游消费：

  1. 楼层/标高识别（补 section-z：标高 token → 真实层高，自动打底人工校正）
  2. 轴号 → 跨图拼接配准（轴号 token 作锚点）
  3. 构件名/房间名/说明 → 语义树

设计与 ``spotting`` 一致：契约先行、后端可插拔、懒加载重依赖、无 GPU/权重时
优雅降级 + 离线 mock，使「渲染 → OCR → 分类」整链路在 CI 下可端到端跑通。

纪律（用户强调）：**置信门槛 + 人工复核**，低置信不自动采纳——读错比缺失更糟。
"""
from .types import OcrBackend, OcrResult, TextToken
from .classify import classify_text
from .consume import axis_anchors, elevation_candidates, space_labels
from .service import ocr_drawing, run_ocr

__all__ = [
    "OcrBackend",
    "OcrResult",
    "TextToken",
    "classify_text",
    "axis_anchors",
    "elevation_candidates",
    "space_labels",
    "ocr_drawing",
    "run_ocr",
]
