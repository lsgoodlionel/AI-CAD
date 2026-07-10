"""
多模态（图像）消息转换与校验层 —— A-09。

各 LLM 提供商的图像消息格式互不相同，本模块提供一个「提供商无关的规范图像块」，
并把它翻译成 Anthropic / OpenAI 兼容 / Ollama 各自的原生格式。

规范图像块（A-11 调用方在 messages[*].content 列表里塞入的形态）：
    文本块 : {"type": "text",  "text": "..."}
    图像块 : {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "<b64>"}}
             {"type": "image", "source": {"type": "url",    "url": "https://..."}}

一条多模态消息形如：
    {"role": "user", "content": [<block>, <block>, ...]}
纯文本消息（content 为 str）保持原样，text-only 调用零差异回归。

────────────────────────────────────────────────────────────────────────
⚠️ 硬约束（VLM 语义边界，务必遵守）
    VLM（视觉大模型）只用于**语义理解**（判断构件类别、用途、图面说明含义等）。
    严禁将 VLM 用于**精确计数 / 坐标 / 尺寸测量**——这些必须走确定性几何管线
    （ezdxf / IfcOpenShell / YOLO 检测框），VLM 的输出在这些维度上不可信。
    本模块只负责「传输层」：把图像安全地送进模型，不做任何几何解读。

⚠️ 闭源模型内部会对大图**降采样**，坐标/尺寸会因此失真，进一步佐证上述约束：
    - Claude（Anthropic）：最长边压到 1568px
    - GPT-4o（OpenAI）  ：最短边压到 768px
    真正的「大图切图 / 分块」由 A-12 负责，本任务只保证传输层不 OOM、格式正确。
"""
from __future__ import annotations

import base64
import binascii
from io import BytesIO
from typing import Any

# ── 传输层校验阈值（防 OOM，非模型精度阈值）──────────────────────────
# 单张图 base64 解码后最大字节数。超过则拒绝，避免把超大图读进内存。
MAX_IMAGE_BYTES: int = 20 * 1024 * 1024  # 20 MiB
# 单张图任一边最大像素。大图切分是 A-12 的职责，这里只兜底拦截异常大图。
MAX_IMAGE_DIMENSION: int = 8192  # px

SUPPORTED_MEDIA_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif"}
)


class VisionMessageError(ValueError):
    """多模态消息非法（格式错误、图像超限、URL 不受支持等）。"""


# ── 检测 ────────────────────────────────────────────────────────────

def _is_image_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "image"


def message_has_image(message: dict) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(_is_image_block(b) for b in content)


def messages_have_images(messages: list[dict]) -> bool:
    """任一消息含图像块时返回 True。text-only 时为 False（走零差异路径）。"""
    return any(message_has_image(m) for m in messages)


# ── 校验 ────────────────────────────────────────────────────────────

def _estimated_bytes(b64_data: str) -> int:
    """由 base64 字符串长度估算解码后字节数，先于 decode 判断以防 OOM。"""
    return (len(b64_data) * 3) // 4


def _validate_dimensions(raw: bytes) -> None:
    from PIL import Image  # 延迟导入，避免非视觉路径加载 PIL

    try:
        with Image.open(BytesIO(raw)) as img:
            width, height = img.size
    except Exception as exc:  # noqa: BLE001 - 统一转为领域异常
        raise VisionMessageError(f"图像无法解析: {exc}") from exc
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise VisionMessageError(
            f"图像尺寸 {width}x{height} 超过上限 {MAX_IMAGE_DIMENSION}px（大图切分见 A-12）"
        )


def _validate_base64_source(source: dict) -> None:
    media_type = source.get("media_type")
    data = source.get("data")
    if media_type not in SUPPORTED_MEDIA_TYPES:
        raise VisionMessageError(f"不支持的图像类型: {media_type!r}")
    if not isinstance(data, str) or not data:
        raise VisionMessageError("图像 base64 数据为空")
    if _estimated_bytes(data) > MAX_IMAGE_BYTES:
        raise VisionMessageError(f"图像超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MiB 上限")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VisionMessageError(f"图像 base64 非法: {exc}") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise VisionMessageError(f"图像超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MiB 上限")
    _validate_dimensions(raw)


def _validate_source(source: Any) -> None:
    if not isinstance(source, dict):
        raise VisionMessageError("图像块缺少 source")
    kind = source.get("type")
    if kind == "base64":
        _validate_base64_source(source)
    elif kind == "url":
        url = source.get("url")
        if not isinstance(url, str) or not url:
            raise VisionMessageError("图像 URL 为空")
    else:
        raise VisionMessageError(f"未知图像 source 类型: {kind!r}")


# ── 翻译（均返回新对象，不修改入参）────────────────────────────────

def to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Anthropic 原生 content blocks；规范格式与其一致，校验后透传。"""
    result: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(dict(msg))
            continue
        blocks: list[dict] = []
        for block in content:
            if _is_image_block(block):
                _validate_source(block.get("source"))
                blocks.append({"type": "image", "source": dict(block["source"])})
            else:
                blocks.append(dict(block))
        result.append({**msg, "content": blocks})
    return result


def _image_block_to_openai(block: dict) -> dict:
    source = block.get("source")
    _validate_source(source)
    if source["type"] == "base64":
        url = f"data:{source['media_type']};base64,{source['data']}"
    else:
        url = source["url"]
    return {"type": "image_url", "image_url": {"url": url}}


def to_openai_messages(messages: list[dict]) -> list[dict]:
    """OpenAI 兼容：图像块 → {"type":"image_url","image_url":{"url":...}}。"""
    result: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(dict(msg))
            continue
        blocks = [
            _image_block_to_openai(b) if _is_image_block(b) else dict(b)
            for b in content
        ]
        result.append({**msg, "content": blocks})
    return result


def _message_to_ollama(msg: dict) -> dict:
    """Ollama：content 为纯文本 str，图像以 images:[base64] 附加（不支持 URL）。"""
    content = msg.get("content")
    if not isinstance(content, list):
        return dict(msg)
    texts: list[str] = []
    images: list[str] = []
    for block in content:
        if _is_image_block(block):
            source = block.get("source")
            _validate_source(source)
            if source["type"] != "base64":
                raise VisionMessageError("Ollama 仅支持 base64 图像，不支持 URL")
            images.append(source["data"])
        elif isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text", "")))
    out: dict = {**msg, "content": "\n".join(texts)}
    if images:
        out["images"] = images
    return out


def to_ollama_messages(messages: list[dict]) -> list[dict]:
    return [_message_to_ollama(m) for m in messages]
