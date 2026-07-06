"""
视觉/OCR 引擎：从图纸文件提取结构化文本和视觉信息。
优先级：
  1. ezdxf     — DWG/DXF 精准解析（最可靠）
  2. pymupdf   — PDF 文本提取（包含扫描件 OCR fallback）
  3. PaddleOCR — 图像 OCR（作为兜底，可选安装）
  4. 基础检查  — 文件大小/类型 (始终运行)
"""
import io
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor

from core.storage import get_minio, settings
from .base import BaseEngine, DrawingContext, AIIssue, IssueSeverity
from .dwg_support import ensure_dxf

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)

MAX_TEXT_CHARS = 20_000
MIN_FILE_SIZE_KB = 10


def _download_bytes(file_key: str) -> bytes:
    """同步下载（在线程池执行，不阻塞事件循环）"""
    client = get_minio()
    bucket = settings.minio_bucket_drawings
    response = client.get_object(bucket, file_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _extract_dxf(data: bytes) -> tuple[str, list[str]]:
    """用 ezdxf 解析 DWG/DXF，提取 MTEXT/TEXT 实体。"""
    import ezdxf
    doc = ezdxf.read(io.BytesIO(data))
    msp = doc.modelspace()
    texts: list[str] = []
    for entity in msp:
        if entity.dxftype() in ("TEXT", "MTEXT"):
            try:
                t = entity.plain_mtext() if entity.dxftype() == "MTEXT" else entity.dxf.text
                if t and t.strip():
                    texts.append(t.strip())
            except Exception:
                pass
    return "\n".join(texts)[:MAX_TEXT_CHARS], texts


def _extract_pdf(data: bytes) -> tuple[str, dict]:
    """用 pymupdf 提取 PDF 文本，返回合并文本和元数据。"""
    import fitz  # pymupdf
    doc = fitz.open(stream=data, filetype="pdf")
    pages_text: list[str] = []
    meta = {"page_count": len(doc)}
    for page in doc:
        pages_text.append(page.get_text())
    combined = "\n".join(pages_text)[:MAX_TEXT_CHARS]
    doc.close()
    return combined, meta


def _extract_ocr(data: bytes) -> str:
    """PaddleOCR 对图像/扫描 PDF 做 OCR（可选安装）。"""
    from paddleocr import PaddleOCR
    import numpy as np
    from PIL import Image

    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    arr = np.array(img)
    result = ocr.ocr(arr, cls=True)
    lines = [r[1][0] for page in (result or []) for r in (page or [])]
    return "\n".join(lines)[:MAX_TEXT_CHARS]


def _extract_cad(data: bytes, file_ext: str, metadata: dict, issues: list[AIIssue]) -> str:
    """DWG/DXF 提取：DWG 先经 ensure_dxf（ODA 转换），成功按 DXF 解析；
    未配置/转换失败降级为 INFO 提示，不再对二进制 DWG 盲目 ezdxf.read。"""
    dxf_data, _effective_ext, warning = ensure_dxf(data, file_ext)
    if warning:
        issues.append(AIIssue(
            engine="ocr", severity=IssueSeverity.INFO,
            description=warning,
            category="引擎配置",
            suggestion="安装 ODA File Converter 并配置 ODA_CONVERTER_PATH，或上传 DXF/PDF 版本图纸",
        ))
        return ""
    try:
        extracted, _ = _extract_dxf(dxf_data)
    except ImportError:
        logger.warning("[VisionEngine] ezdxf 未安装，跳过 DXF 解析")
        issues.append(AIIssue(
            engine="ocr", severity=IssueSeverity.INFO,
            description="ezdxf 未安装，DXF/DWG 文件无法深度解析，建议管理员安装 ezdxf",
            category="引擎配置",
        ))
        return ""
    except Exception as e:
        logger.warning("[VisionEngine] DXF 解析失败: %s", e)
        issues.append(AIIssue(
            engine="ocr", severity=IssueSeverity.MINOR,
            description=f"DXF 文件解析遇到问题：{e}",
            category="文件质量",
        ))
        return ""
    is_converted = file_ext == "dwg" and dxf_data is not data
    metadata["parser"] = "oda+ezdxf" if is_converted else "ezdxf"
    logger.info("[VisionEngine] %s 提取 %d 字符", metadata["parser"], len(extracted))
    return extracted


def _sync_extract(file_key: str, file_ext: str, file_size_kb: int) -> tuple[str, dict, list[AIIssue], bytes]:
    """
    同步提取主函数（在线程池中调用）。
    返回 (extracted_text, ocr_metadata, preliminary_issues)
    """
    issues: list[AIIssue] = []
    metadata: dict = {"file_ext": file_ext, "file_size_kb": file_size_kb}
    extracted = ""
    raw_data: bytes = b""

    # ── 基础文件校验（始终运行）────────────────────────────────
    if file_size_kb < MIN_FILE_SIZE_KB:
        issues.append(AIIssue(
            engine="ocr",
            severity=IssueSeverity.MAJOR,
            description=f"文件大小仅 {file_size_kb}KB，疑似空白或损坏文件",
            category="文件质量",
            suggestion="确认图纸文件内容完整后重新上传",
        ))

    # ── 下载文件 ───────────────────────────────────────────────
    try:
        data = _download_bytes(file_key)
    except Exception as e:
        logger.error("[VisionEngine] 文件下载失败: %s", e)
        issues.append(AIIssue(
            engine="ocr",
            severity=IssueSeverity.CRITICAL,
            description=f"图纸文件下载失败，无法执行 AI 审查（{e}）",
            category="文件访问",
        ))
        return extracted, metadata, issues, b""

    raw_data = data
    metadata["actual_size_bytes"] = len(data)

    # ── DWG/DXF 路径 ───────────────────────────────────────────
    if file_ext in ("dwg", "dxf"):
        extracted = _extract_cad(data, file_ext, metadata, issues)

    # ── PDF 路径 ───────────────────────────────────────────────
    elif file_ext == "pdf":
        try:
            extracted, pdf_meta = _extract_pdf(data)
            metadata.update(pdf_meta)
            metadata["parser"] = "pymupdf"
            logger.info("[VisionEngine] PDF 提取 %d 字符，共 %d 页",
                        len(extracted), pdf_meta.get("page_count", 0))

            # 扫描版 PDF：文本量过少时尝试 PaddleOCR
            if len(extracted.strip()) < 200:
                logger.info("[VisionEngine] PDF 文本量不足，尝试 PaddleOCR")
                try:
                    extracted = _extract_ocr(data)
                    metadata["parser"] = "pymupdf+paddleocr"
                except ImportError:
                    issues.append(AIIssue(
                        engine="ocr", severity=IssueSeverity.INFO,
                        description="疑似扫描版 PDF，文本提取量不足，建议安装 PaddleOCR 提升识别率",
                        category="引擎配置",
                    ))
                except Exception as e:
                    logger.warning("[VisionEngine] PaddleOCR 失败: %s", e)
        except ImportError:
            logger.warning("[VisionEngine] pymupdf 未安装")
            issues.append(AIIssue(
                engine="ocr", severity=IssueSeverity.INFO,
                description="pymupdf 未安装，PDF 文本提取不可用，建议管理员安装",
                category="引擎配置",
            ))
        except Exception as e:
            logger.warning("[VisionEngine] PDF 解析失败: %s", e)

    # ── YOLOv8 图元检测（PDF / 图像文件）─────────────────────────
    if file_ext in ("pdf", "png", "jpg", "jpeg", "tif", "tiff") and raw_data:
        from .yolo_detector import detect_drawing_elements
        _, yolo_issues = detect_drawing_elements(raw_data, file_ext)
        issues.extend(yolo_issues)
        metadata["yolo_ran"] = True

    # ── 文本质量检查 ───────────────────────────────────────────
    if extracted:
        if len(extracted.strip()) < 100:
            issues.append(AIIssue(
                engine="ocr", severity=IssueSeverity.MINOR,
                description="图纸提取文本量极少（< 100 字符），设计说明内容可能不完整",
                category="内容质量",
                suggestion="确认图纸包含完整的设计说明文字",
            ))

        # 检查标题栏完整性
        title_block_keywords = ["图纸编号", "设计人", "审核", "日期", "比例", "工程名称"]
        found = [kw for kw in title_block_keywords if kw in extracted]
        missing = [kw for kw in title_block_keywords if kw not in extracted]
        metadata["title_block_found"] = found
        if len(missing) > 3:
            issues.append(AIIssue(
                engine="ocr", severity=IssueSeverity.MAJOR,
                description=f"标题栏信息不完整，缺少：{'、'.join(missing[:4])}",
                category="图纸规范",
                regulation_ref="企业制图规范 Q/CAD-001 第5章",
                suggestion="补充完整标题栏信息（设计人、审核、日期、比例等）",
            ))

    return extracted, metadata, issues, raw_data


class VisionEngine(BaseEngine):
    engine_name = "ocr"

    async def analyze(self, ctx: DrawingContext, db) -> list[AIIssue]:
        file_size_kb = 0
        try:
            row = await db.fetch_one(
                "SELECT file_size_kb FROM drawings WHERE id=:drawing_id",
                {"drawing_id": ctx.drawing_id},
            )
            file_size_kb = row["file_size_kb"] if row else 0
        except Exception:
            pass

        loop = asyncio.get_event_loop()
        try:
            extracted, metadata, issues, _ = await loop.run_in_executor(
                _executor,
                _sync_extract,
                ctx.file_key, ctx.file_ext, file_size_kb,
            )
        except Exception as e:
            logger.error("[VisionEngine] 执行异常: %s", e)
            return [AIIssue(
                engine=self.engine_name,
                severity=IssueSeverity.CRITICAL,
                description=f"视觉引擎运行异常：{e}",
                category="引擎故障",
            )]

        # 填充上下文供后续引擎使用
        ctx.extracted_text = extracted
        ctx.ocr_metadata = metadata

        logger.info("[VisionEngine] 提取完成，文本 %d 字符，初步问题 %d 条",
                    len(extracted), len(issues))
        return issues
