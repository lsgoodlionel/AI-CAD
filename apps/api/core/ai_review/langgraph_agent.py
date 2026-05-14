"""
LangGraph 多轮推理代理：三步审查流水线。
  Step 1 — identify  : 从图纸信息识别潜在问题点
  Step 2 — lookup    : 针对问题点补充检索规范条文
  Step 3 — synthesize: 综合生成最终审查意见

graceful degradation：langgraph 未安装时自动回退到单步 LLM 调用。
"""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Prompt 定义 ─────────────────────────────────────────────────

_IDENTIFY_SYSTEM = (
    "你是建筑图纸合规审查专家。请从图纸信息中识别3-5个最重要的潜在合规问题点，"
    "每条简洁描述关注点（如“钢筋锚固长度”、“消防疏散距离”等）。"
    "仅输出 JSON 数组，格式：[\"问题点1\", \"问题点2\"]，不要输出其他内容。"
)

_LOOKUP_SYSTEM = (
    "你是建筑规范专家。针对给定问题点，列出最相关的规范条文要求（各条文一行，含条文编号）。"
    "仅输出 JSON 数组，格式：[\"GB50010-2010 第X条：...\", ...]，不要输出其他内容。"
)

_SYNTHESIS_SYSTEM = (
    "你是建筑图纸合规审查专家，擅长结构/建筑/机电专业图纸审查。"
    "请综合图纸信息、问题点和规范要求，生成最终审查意见，仅输出 JSON，格式如下：\n"
    '{"issues":[{"severity":"critical|major|minor|info","description":"问题描述（中文，具体）",'
    '"regulation_ref":"规范条文引用","suggestion":"修改建议"}],"summary":"整体评价（1-2句）"}\n'
    "如无问题，issues 返回空数组。不要输出 JSON 以外的任何内容。"
)

_FALLBACK_SYSTEM = (
    "你是建筑图纸合规审查专家，擅长结构/建筑/机电专业图纸审查。"
    "请根据规范参考对图纸信息进行合规性分析，仅输出 JSON，格式：\n"
    '{"issues":[{"severity":"critical|major|minor|info","description":"问题描述",'
    '"regulation_ref":"规范引用","suggestion":"修改建议"}],"summary":"整体评价"}\n'
    "如无问题，issues 返回空数组。"
)


# ── 辅助函数 ────────────────────────────────────────────────────

async def _call_llm(router: Any, engine: str, system: str, user: str) -> str:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    response = await router.route(engine, messages)
    return response.content.strip()


def _parse_json(text: str, default: Any) -> Any:
    try:
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception:
        return default


# ── LangGraph State ─────────────────────────────────────────────

def _make_state(drawing_info: str, regulations: str) -> dict:
    return {
        "drawing_info": drawing_info,
        "regulations": regulations,
        "problem_points": [],
        "regulation_refs": [],
        "final_issues": [],
        "summary": "",
    }


# ── 节点函数 ────────────────────────────────────────────────────

async def _identify_node(state: dict, router: Any) -> dict:
    prompt = (
        f"图纸信息：\n{state['drawing_info']}\n\n"
        f"已检索规范（摘要）：\n{state['regulations'][:1000]}"
    )
    try:
        raw = await _call_llm(router, "rag_qa", _IDENTIFY_SYSTEM, prompt)
        points = _parse_json(raw, [])
        if not isinstance(points, list):
            points = []
    except Exception as e:
        logger.warning("[LangGraphAgent] 问题识别失败: %s", e)
        points = []
    logger.debug("[LangGraphAgent] step1 问题点: %s", points)
    return {**state, "problem_points": points}


async def _lookup_node(state: dict, router: Any) -> dict:
    points_text = "\n".join(f"- {p}" for p in state.get("problem_points", []))
    prompt = (
        f"图纸信息摘要：\n{state['drawing_info'][:600]}\n\n"
        f"关注问题点：\n{points_text}"
    )
    try:
        raw = await _call_llm(router, "rag_rewriter", _LOOKUP_SYSTEM, prompt)
        refs = _parse_json(raw, [])
        if not isinstance(refs, list):
            refs = []
    except Exception as e:
        logger.warning("[LangGraphAgent] 规范检索失败: %s", e)
        refs = []
    logger.debug("[LangGraphAgent] step2 规范条文: %d 条", len(refs))
    return {**state, "regulation_refs": refs}


async def _synthesize_node(state: dict, router: Any) -> dict:
    refs_text = "\n".join(state.get("regulation_refs", []))
    points_text = "\n".join(f"- {p}" for p in state.get("problem_points", []))
    prompt = (
        f"图纸信息：\n{state['drawing_info']}\n\n"
        f"关注问题点：\n{points_text}\n\n"
        f"相关规范条文：\n{refs_text}\n\n"
        f"向量检索规范（原始）：\n{state['regulations'][:1500]}"
    )
    try:
        raw = await _call_llm(router, "rag_qa", _SYNTHESIS_SYSTEM, prompt)
        data = _parse_json(raw, {"issues": [], "summary": ""})
    except Exception as e:
        logger.warning("[LangGraphAgent] 综合分析失败: %s", e)
        data = {"issues": [], "summary": ""}
    return {**state, "final_issues": data.get("issues", []), "summary": data.get("summary", "")}


# ── 主入口 ──────────────────────────────────────────────────────

async def run_langgraph_agent(
    drawing_info: str,
    regulations: str,
    router: Any,
) -> tuple[list[dict], str]:
    """
    运行三步推理流水线。
    Returns: (issues_list, summary_str)
    langgraph 未安装时自动降级为直接 LLM 调用。
    """
    try:
        from langgraph.graph import StateGraph, END
        _use_graph = True
    except ImportError:
        logger.info("[LangGraphAgent] langgraph 未安装，降级为直接 LLM 调用")
        _use_graph = False

    if _use_graph:
        try:
            result = await _run_with_graph(drawing_info, regulations, router)
            return result["final_issues"], result["summary"]
        except Exception as e:
            logger.warning("[LangGraphAgent] StateGraph 执行失败，降级: %s", e)

    return await _run_fallback(drawing_info, regulations, router)


async def _run_with_graph(drawing_info: str, regulations: str, router: Any) -> dict:
    from langgraph.graph import StateGraph, END

    async def identify(state: dict) -> dict:
        return await _identify_node(state, router)

    async def lookup(state: dict) -> dict:
        return await _lookup_node(state, router)

    async def synthesize(state: dict) -> dict:
        return await _synthesize_node(state, router)

    workflow = StateGraph(dict)
    workflow.add_node("identify", identify)
    workflow.add_node("lookup", lookup)
    workflow.add_node("synthesize", synthesize)
    workflow.set_entry_point("identify")
    workflow.add_edge("identify", "lookup")
    workflow.add_edge("lookup", "synthesize")
    workflow.add_edge("synthesize", END)

    graph = workflow.compile()
    result = await graph.ainvoke(_make_state(drawing_info, regulations))
    logger.info("[LangGraphAgent] 三步推理完成，%d 条问题", len(result.get("final_issues", [])))
    return result


async def _run_fallback(drawing_info: str, regulations: str, router: Any) -> tuple[list[dict], str]:
    prompt = f"图纸信息：\n{drawing_info}\n\n规范参考：\n{regulations[:2000]}"
    try:
        raw = await _call_llm(router, "rag_qa", _FALLBACK_SYSTEM, prompt)
        data = _parse_json(raw, {"issues": [], "summary": ""})
        return data.get("issues", []), data.get("summary", "")
    except Exception as e:
        logger.warning("[LangGraphAgent] fallback LLM 调用失败: %s", e)
        return [], ""
