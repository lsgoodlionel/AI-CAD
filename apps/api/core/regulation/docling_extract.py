"""Docling（IBM，MIT 协议）PDF/Office 文档结构化提取封装。

为什么加它：现有 `extract_text_from_pdf`（services/regulation_importer.py）
用 pymupdf4llm→pymupdf 逐级降级，两者都是「按页抽文本流」，对**多栏排版**
（规范正文常见双栏）容易把左右两栏交错拼接、对**表格**（构件截面表、荷载表
等强条文常见形式）没有结构化保真（表格坍缩成散乱文本行），下游 NLP 提取
流水线（Haiku 分类→Sonnet 深提取）再从乱序文本里抠条文编号/表格数据，
准确率天然受限。

docling 用版面分析模型识别阅读顺序与表格结构，导出 Markdown 时表格保留为
Markdown 表格语法、多栏按正确阅读顺序拼接——理论上更适合规范 PDF 这类版面。
是否真的更好由 `scripts/regulation/parse_ab_eval.py` 的离线 A/B 评测说了算，
本模块只负责「有 docling 就能安全用，没有就绝不报错、也绝不改变默认行为」。

优雅降级契约（与 core/model3d/ocr 各 backend 同范式）：懒加载 + 单例缓存 +
负例缓存（避免每次调用重复 import 失败探测），未安装/转换失败一律返回
``None``，调用方（`services/regulation_importer.py::extract_text_from_pdf`）
按契约继续走 pymupdf4llm→pymupdf 原有降级链，不抛异常。

⚠️ 本次未安装 docling 依赖（MIT license 干净，但是否入部署镜像待 A/B 评测
结论 + 你拍板决定，避免不必要的镜像体积增长）。下面的真实调用路径**未接入
真实 docling 环境验证**，见 `extract_with_docling` 文档字符串内 TODO。
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_load_failed = False
_converter_singleton = None


def _get_converter():
    """懒加载 docling ``DocumentConverter`` 单例；失败缓存负例（下次直接短路）。"""
    global _converter_singleton, _load_failed
    if _converter_singleton is not None:
        return _converter_singleton
    if _load_failed:
        return None
    try:
        from docling.document_converter import DocumentConverter

        _converter_singleton = DocumentConverter()
        return _converter_singleton
    except Exception as exc:  # noqa: BLE001 — 未安装/加载失败一律降级，不影响主流程
        logger.info("[regulation.docling] docling 不可用，降级到 pymupdf4llm: %s", exc)
        _load_failed = True
        return None


def is_available() -> bool:
    """docling 是否可用（已安装且可正常构造 ``DocumentConverter``）。"""
    return _get_converter() is not None


def extract_with_docling(file_bytes: bytes, filename: str = "document.pdf") -> str | None:
    """PDF/Office → Markdown（docling 版面分析，表格/多栏结构保真）。

    未安装、构造失败或转换过程任何异常均返回 ``None``（不抛出）——本函数是
    ``extract_text`` 链路的**候选前段**，失败必须无声降级到既有
    pymupdf4llm→pymupdf 路径，不能让规范导入因为一个可选依赖而中断。

    TODO（真实接线，需装 docling 后逐条验证，当前是保守的最大公约数写法）：
      1. 确认所装 docling 版本的内存流 API（``DocumentStream`` 或等价物），
         用 ``BytesIO`` 直接喂而非落临时文件，省一次磁盘 IO；当前用
         ``tempfile`` 是为了对任何「只支持路径输入」的 docling 版本都成立。
      2. 确认 ``result.document.export_to_markdown()`` 的表格 Markdown 语法
         与下游 ``split_into_paragraphs`` 的条文编号正则
         （``regulation_importer._ARTICLE_PATTERN``）兼容——docling 的表格块
         可能打断条文编号所在行的连续性，需要 A/B 评测
         （``scripts/regulation/parse_ab_eval.py``）实测后决定是否要在
         docling 输出上加一道「表格块隔离」预处理再喂给现有分段逻辑。
      3. 确认大文件（整本规范书，常见 200+ 页）的转换耗时是否需要挪到
         Celery 异步任务（现有 pymupdf4llm 路径是同步调用，见
         `services/regulation_importer.py::extract_text_from_pdf` 的调用方
         `import_regulation_file`；若 docling 显著更慢需要评估是否阻塞导入
         任务的整体超时预算）。
    """
    converter = _get_converter()
    if converter is None:
        return None

    suffix = Path(filename).suffix or ".pdf"
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            result = converter.convert(tmp.name)
            markdown = result.document.export_to_markdown()
            if not markdown or not markdown.strip():
                logger.warning("[regulation.docling] 转换结果为空，降级到 pymupdf4llm")
                return None
            return markdown
    except Exception as exc:  # noqa: BLE001 — 候选路径优雅降级，不影响主流程
        logger.warning("[regulation.docling] 提取失败，降级到 pymupdf4llm: %s", exc)
        return None
