"""
YOLOv8 图元检测器：识别图纸中的钢筋符号、预留洞口、标题栏等图元。
graceful degradation：ultralytics 未安装时返回空列表 + INFO 提示。
"""
import io
import logging
from dataclasses import dataclass

from .base import AIIssue, IssueSeverity

logger = logging.getLogger(__name__)

# 训练好的图纸图元模型路径（不存在时降级为通用 yolov8n.pt）
_MODEL_PATH = "data/models/drawing_elements.pt"
_CONF_THRESHOLD = 0.45


@dataclass
class Detection:
    label: str
    confidence: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 归一化坐标


def _bytes_to_image(data: bytes):  # type: ignore[return]
    """将 PDF/图像字节转为 PIL Image（取第一页）。"""
    # 优先尝试 pymupdf（PDF → 栅格化图像）
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        page = doc[0]
        pix = page.get_pixmap(dpi=150)
        from PIL import Image
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    except Exception:
        pass

    # 回退到直接打开图像
    from PIL import Image
    return Image.open(io.BytesIO(data)).convert("RGB")


def _run_yolo(image_bytes: bytes) -> list[Detection]:
    """运行 YOLOv8 检测，返回 Detection 列表。"""
    from ultralytics import YOLO
    import os

    model_path = _MODEL_PATH if os.path.exists(_MODEL_PATH) else "yolov8n.pt"
    model = YOLO(model_path)

    img = _bytes_to_image(image_bytes)
    results = model(img, conf=_CONF_THRESHOLD, verbose=False)

    detections: list[Detection] = []
    for result in results:
        if result.boxes is None:
            continue
        names = result.names
        for box in result.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            xyxyn = box.xyxyn[0].tolist()
            detections.append(Detection(
                label=names.get(cls_id, f"cls_{cls_id}"),
                confidence=conf,
                box=(xyxyn[0], xyxyn[1], xyxyn[2], xyxyn[3]),
            ))
    return detections


def detect_drawing_elements(
    file_bytes: bytes,
    file_ext: str,
) -> tuple[list[Detection], list[AIIssue]]:
    """
    对图纸图像/PDF 运行 YOLOv8 图元检测。

    Returns:
        (detections, issues)
        - ultralytics 未安装 → ([], [INFO issue])
        - 不支持的文件类型   → ([], [])
        - 检测执行异常       → ([], [])
    """
    if file_ext not in ("pdf", "png", "jpg", "jpeg", "tif", "tiff"):
        return [], []

    try:
        detections = _run_yolo(file_bytes)
    except ImportError:
        logger.info("[YoloDetector] ultralytics 未安装，跳过图元检测")
        return [], [AIIssue(
            engine="ocr",
            severity=IssueSeverity.INFO,
            description="YOLOv8 图元检测器未安装（ultralytics），建议安装以增强扫描图纸识别能力",
            category="引擎配置",
        )]
    except Exception as e:
        logger.warning("[YoloDetector] 图元检测异常: %s", e)
        return [], []

    issues: list[AIIssue] = []
    counts: dict[str, int] = {}
    for d in detections:
        counts[d.label] = counts.get(d.label, 0) + 1

    # ── 标题栏校验 ──────────────────────────────────────────────
    if len(detections) > 5 and counts.get("title_block", 0) == 0:
        issues.append(AIIssue(
            engine="ocr",
            severity=IssueSeverity.MAJOR,
            description="YOLOv8 未检测到标题栏区域，请确认图纸包含符合企业规范的标题栏",
            category="图纸规范",
            suggestion="补充标准标题栏（含图纸编号、设计人、审核人、日期、比例等字段）",
        ))

    # ── 钢筋符号密度异常 ────────────────────────────────────────
    rebar_count = counts.get("rebar_symbol", 0)
    if rebar_count > 200:
        issues.append(AIIssue(
            engine="ocr",
            severity=IssueSeverity.INFO,
            description=f"检测到 {rebar_count} 个钢筋符号，请确认钢筋布置图与结构计算书一致",
            category="结构专业",
        ))

    # ── 预留洞口记录 ────────────────────────────────────────────
    hole_count = counts.get("reserved_hole", 0)
    if hole_count > 0:
        logger.info("[YoloDetector] 检测到 %d 个预留洞口", hole_count)

    logger.info("[YoloDetector] 共检测到 %d 个图元，类别分布: %s", len(detections), counts)
    return detections, issues
