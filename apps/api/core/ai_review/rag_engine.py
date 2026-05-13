"""
RAG + LLM 引擎：语义检索 + 大模型分析。
- Chroma 向量库检索相关规范片段
- ModelRouter 调用 LLM（任意配置的后端）进行语义审查
- 若 Chroma 为空或 LLM 不可用，引擎静默跳过（不报错）
"""
import json
import logging

from core.config import settings
from core.llm.router import ModelRouter
from .base import BaseEngine, DrawingContext, AIIssue, IssueSeverity

logger = logging.getLogger(__name__)

_SEVERITY_MAP = {
    "critical": IssueSeverity.CRITICAL,
    "major":    IssueSeverity.MAJOR,
    "minor":    IssueSeverity.MINOR,
    "info":     IssueSeverity.INFO,
}

_SYSTEM_PROMPT = """\
你是一名专业的建筑图纸合规审查专家，擅长结构/建筑/机电专业图纸审查。
请根据提供的规范参考，对图纸信息进行合规性分析，仅输出 JSON，格式如下：
{
  "issues": [
    {
      "severity": "critical|major|minor|info",
      "description": "问题描述（中文，简洁具体）",
      "regulation_ref": "规范条文引用（如 GB50010-2010 第8.2.1条）",
      "suggestion": "修改建议"
    }
  ],
  "summary": "整体评价（1-2句）"
}
如无问题，issues 返回空数组。不要输出 JSON 以外的任何内容。\
"""


def _build_user_prompt(ctx: DrawingContext, regulations: str) -> str:
    text_excerpt = ctx.extracted_text[:3000] if ctx.extracted_text else "（未提取到图纸文本内容）"
    return (
        f"专业：{ctx.discipline}\n"
        f"图纸编号：{ctx.drawing_no}\n"
        f"标题：{ctx.title or '（未填写）'}\n"
        f"版次：{ctx.version}\n"
        f"预估影响金额：{ctx.estimated_impact or '未知'} 元\n\n"
        f"相关规范参考（向量检索结果）：\n{regulations}\n\n"
        f"图纸文本摘要（OCR/解析）：\n{text_excerpt}"
    )


async def _query_chroma(ctx: DrawingContext) -> str:
    """从 Chroma 检索相关规范文本，返回合并字符串。"""
    try:
        import chromadb
        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        collection = client.get_or_create_collection("regulation_articles")

        if collection.count() == 0:
            return "（规范知识库为空，跳过语义检索）"

        query_text = f"{ctx.discipline} {ctx.title} {ctx.drawing_no}"
        results = collection.query(
            query_texts=[query_text],
            n_results=5,
            where={"discipline": {"$in": [ctx.discipline, "common"]}},
        )
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        parts = []
        for doc, meta in zip(docs, metas):
            ref = meta.get("article_no", "")
            parts.append(f"[{ref}] {doc[:400]}")
        return "\n\n".join(parts) if parts else "（未检索到相关规范）"

    except ImportError:
        return "（chromadb 未安装，跳过向量检索）"
    except Exception as e:
        logger.debug("[RAGEngine] Chroma 查询失败: %s", e)
        return "（向量检索暂不可用）"


class RAGEngine(BaseEngine):
    engine_name = "rag"

    def __init__(self, db, redis):
        self._db = db
        self._redis = redis
        self._router: ModelRouter | None = None

    def _get_router(self) -> ModelRouter:
        if self._router is None:
            self._router = ModelRouter(self._db, self._redis)
        return self._router

    async def analyze(self, ctx: DrawingContext, db) -> list[AIIssue]:
        # ── 1. 向量检索 ───────────────────────────────────────
        regulations = await _query_chroma(ctx)

        # ── 2. LangGraph 三步推理（或直接 LLM 降级）──────────
        drawing_info = _build_user_prompt(ctx, regulations)
        try:
            from .langgraph_agent import run_langgraph_agent
            raw_issues, _ = await run_langgraph_agent(
                drawing_info=drawing_info,
                regulations=regulations,
                router=self._get_router(),
            )
        except Exception as e:
            logger.warning("[RAGEngine] LangGraph 调用失败，跳过: %s", e)
            return []

        # ── 3. 转换为 AIIssue ─────────────────────────────────
        issues: list[AIIssue] = []
        for item in raw_issues:
            issues.append(AIIssue(
                engine=self.engine_name,
                severity=_SEVERITY_MAP.get(item.get("severity", "info"), IssueSeverity.INFO),
                description=item.get("description", ""),
                category="语义审查",
                regulation_ref=item.get("regulation_ref", ""),
                suggestion=item.get("suggestion", ""),
            ))

        logger.info("[RAGEngine] LangGraph 返回 %d 条语义问题", len(issues))
        return issues
