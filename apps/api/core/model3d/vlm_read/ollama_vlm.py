"""远程 VLM（qwen3.5-vision，经 Ollama `/api/chat`）读图调用 + 端点解析 + 降级。

实测（人工真跑，非本模块自动验证）：给上海大歌剧院一张结构剖面图 PNG，
qwen3.5-vision 能正确判专业(结构)、读出真实标高(-3.200/-4.700/+15.00)、
识别构件(梁/板/柱/基础底板)。这是语义候选源，不是权威真值——见
``core.model3d.vlm_read.types`` 的铁律。

端点安全（关键）：远程地址绝不硬编码、绝不写进任何被 git 跟踪的文件。
``resolve_base_url`` 只从两处取值，优先级如下：
  1. 数据库 ``llm_providers`` 表 name = 'Ollama 远程' 的 ``base_url``
     （复用现有模型路由治理表，管理后台可见、可换，无需改代码）
  2. 环境变量 ``REMOTE_OLLAMA_BASE_URL``
两者皆缺时返回 ``None``，调用方据此优雅降级（backend="none"），绝不抛错
阻断上游、绝不用占位地址顶替。
"""
from __future__ import annotations

import base64
import logging
import os

import httpx

from .parse import parse_vlm_text
from .types import VlmReadResult

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3.5:latest"

_DB_PROVIDER_NAME = "Ollama 远程"
_ENV_BASE_URL = "REMOTE_OLLAMA_BASE_URL"

# 实测：qwen3.5 含 thinking，单图推理 ~25s；给足余量避免大图/排队场景超时
_DEFAULT_TIMEOUT_SEC = 120.0

# 实测：原图 4718×3338/650KB 直接喂 → HTTP 400（超限）；缩到宽/高 ≤1280px（约
# 910×644/79KB，缩放矩阵约 0.27）→ HTTP 200 成功。等比缩放，不放大小图。
_DEFAULT_MAX_DIM_PX = 1280

_PROMPT = (
    "请阅读这张工程图纸，仅回答以下三项，不要输出坐标、数量、具体尺寸：\n"
    "1. 专业：从[建筑/结构/给排水/暖通/电气/道路/景观]中选一个最匹配的，"
    "若无法判断填「unknown」。\n"
    "2. 标高：列出图中能读到的标高数值（米），如 -3.200、+15.00，用顿号分隔；"
    "读不到就填「无」。\n"
    "3. 构件：列出图中能识别的构件类别（如 梁、板、柱、基础、墙、桩等），"
    "用顿号分隔；读不到就填「无」。\n"
    "请严格按下面的格式回答，每项一行：\n"
    "专业：<答案>\n标高：<答案>\n构件：<答案>"
)


async def resolve_base_url() -> str | None:
    """解析远程 VLM 端点：优先 DB ``llm_providers``『Ollama 远程』，否则 env。

    真实地址不在本文件出现——调用方需在部署环境配置其一。
    """
    db_base_url = await _resolve_base_url_from_db()
    if db_base_url:
        return db_base_url
    return os.environ.get(_ENV_BASE_URL) or None


async def _resolve_base_url_from_db() -> str | None:
    """从 ``llm_providers`` 表按 provider 名读 base_url。DB 未连接/未部署该行
    时静默返回 None（不抛错），由 ``resolve_base_url`` 回退到 env。
    """
    try:
        from core.database import database

        row = await database.fetch_one(
            "SELECT base_url FROM llm_providers WHERE name = :name AND is_active = true LIMIT 1",
            {"name": _DB_PROVIDER_NAME},
        )
    except Exception as exc:  # noqa: BLE001 — DB 不可达是常态（CLI/未连接场景），非错误
        logger.debug("[vlm_read] DB 端点解析跳过（回退 env）: %s", exc)
        return None
    if row is None:
        return None
    base_url = row["base_url"] if hasattr(row, "__getitem__") else getattr(row, "base_url", None)
    return base_url or None


def prepare_image(image_bytes: bytes, *, max_dim_px: int = _DEFAULT_MAX_DIM_PX) -> bytes:
    """位图缩放到远程 VLM 可接受尺寸，重编码为 PNG。

    宽/高较大者缩到 ``max_dim_px``，等比缩放；已小于该尺寸的图不放大
    （放大不增加信息，只浪费带宽）。接受任意 PIL 可解码位图（PNG/JPEG 等）。
    """
    import io

    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = image.size
    scale = min(1.0, max_dim_px / max(width, height))
    if scale < 1.0:
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        image = image.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


async def call_vlm_chat(
    image_png_bytes: bytes,
    prompt: str,
    *,
    base_url: str,
    model: str = DEFAULT_MODEL,
    timeout: float = _DEFAULT_TIMEOUT_SEC,
) -> str:
    """POST ``{base_url}/api/chat``，返回 ``message.content`` 原文本。纯 I/O，不做语义解析。"""
    image_b64 = base64.b64encode(image_png_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
        "stream": False,
        # 思考型模型(qwen3.5)默认 num_predict=128 太小,思考未完就截断致 content 空;
        # 给足预算让其产出结论。
        "options": {"temperature": 0.1, "num_predict": 800},
    }
    # 远程网关常经 Cloudflare 前置,拦无浏览器 UA 的 POST(默认 httpx UA 会 403)。
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
        ),
    }
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        response = await client.post(f"{base_url.rstrip('/')}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()

    message = data.get("message") or {}
    content = message.get("content", "")
    if not isinstance(content, str):
        raise ValueError(f"意外的 VLM 响应结构（message.content 非字符串）: {type(content)!r}")

    # qwen3.5 等思考模型把详细推理放独立的 ``thinking`` 字段、精简结论放 content；
    # 标高等语义线索常只出现在 thinking，合并后一并交解析器（parse 侧带语境守卫，
    # 不会因多出的推理文本误抽）。无 thinking 字段时行为不变。
    thinking = message.get("thinking")
    if isinstance(thinking, str) and thinking.strip():
        return f"{content}\n{thinking}" if content.strip() else thinking
    return content


async def read_drawing_vlm(
    image_bytes: bytes,
    *,
    base_url: str | None = None,
    model: str = DEFAULT_MODEL,
    timeout: float = _DEFAULT_TIMEOUT_SEC,
    max_dim_px: int = _DEFAULT_MAX_DIM_PX,
) -> VlmReadResult:
    """对单张位图跑远程 VLM 语义读图：判专业 + 读标高候选 + 识构件候选。

    整链路：解析端点（未传入则走 ``resolve_base_url``）→ 缩放 → 调用 → 解析。
    铁律：仅产出候选 + 置信度，绝不产出计数/坐标/尺寸/QTO。远程不可达、
    端点未配置、超限、解析失败——任一环节失败都优雅降级为
    ``backend="none"``，绝不抛错阻断上游、绝不编造结果。
    """
    resolved_base_url = base_url or await resolve_base_url()
    if not resolved_base_url:
        return VlmReadResult(
            backend="none",
            model=model,
            warnings=(
                f"未配置远程 VLM 端点（DB llm_providers『{_DB_PROVIDER_NAME}』"
                f"与环境变量 {_ENV_BASE_URL} 均未设置）",
            ),
        )

    try:
        scaled = prepare_image(image_bytes, max_dim_px=max_dim_px)
    except Exception as exc:  # noqa: BLE001 — 非法/损坏图像，降级不阻断
        logger.warning("[vlm_read] 图像预处理失败: %s", exc)
        return VlmReadResult(backend="none", model=model, warnings=(f"图像预处理失败: {exc}",))

    try:
        raw_text = await call_vlm_chat(
            scaled, _PROMPT, base_url=resolved_base_url, model=model, timeout=timeout
        )
    except Exception as exc:  # noqa: BLE001 — 网络/超时/服务异常一律降级，不阻断建模
        logger.warning("[vlm_read] 远程 VLM 调用失败: %s", exc)
        return VlmReadResult(backend="none", model=model, warnings=(f"远程 VLM 调用失败: {exc}",))

    return parse_vlm_text(raw_text, model=model)
