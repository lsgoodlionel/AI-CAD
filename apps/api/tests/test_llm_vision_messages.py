"""
A-09 多模态（图像）消息支持测试。

覆盖：
- 三家 provider 的多模态 payload 序列化正确（Anthropic content blocks /
  OpenAI image_url / Ollama images）
- text-only 回归零差异
- 图像尺寸 / 大小 / 格式校验
"""
from __future__ import annotations

import base64
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from core.llm.providers import vision
from core.llm.providers.base import ModelParams
from core.llm.providers.vision import (
    MAX_IMAGE_DIMENSION,
    VisionMessageError,
)
from core.llm.providers.anthropic_provider import AnthropicProvider
from core.llm.providers.openai_compat import OpenAICompatProvider
from core.llm.providers.ollama_provider import OllamaProvider


# ── 工具 ────────────────────────────────────────────────────────────

def _png_b64(width: int = 4, height: int = 4) -> str:
    img = Image.new("RGB", (width, height), (200, 120, 60))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _image_message(b64: str, text: str = "这是什么构件？") -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            },
        ],
    }


_PARAMS = ModelParams(model_id="test-model", max_tokens=64)


# ── 检测 ────────────────────────────────────────────────────────────

def test_messages_have_images_true_for_image_block():
    assert vision.messages_have_images([_image_message(_png_b64())]) is True


def test_messages_have_images_false_for_text_only():
    msgs = [{"role": "user", "content": "纯文本"}]
    assert vision.messages_have_images(msgs) is False


def test_messages_have_images_false_for_text_block_list():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "只有文字"}]}]
    assert vision.messages_have_images(msgs) is False


# ── 校验 ────────────────────────────────────────────────────────────

def test_rejects_unsupported_media_type():
    src = {"type": "base64", "media_type": "image/tiff", "data": _png_b64()}
    with pytest.raises(VisionMessageError, match="不支持的图像类型"):
        vision._validate_source(src)


def test_rejects_oversized_image_bytes():
    # 构造一个 base64 字符串使估算字节数远超上限，先于 decode 拒绝
    huge = "A" * (vision.MAX_IMAGE_BYTES * 2)
    src = {"type": "base64", "media_type": "image/png", "data": huge}
    with pytest.raises(VisionMessageError, match="上限"):
        vision._validate_source(src)


def test_rejects_oversized_dimensions():
    big = _png_b64(MAX_IMAGE_DIMENSION + 1, 4)
    src = {"type": "base64", "media_type": "image/png", "data": big}
    with pytest.raises(VisionMessageError, match="超过上限"):
        vision._validate_source(src)


def test_rejects_invalid_base64():
    src = {"type": "base64", "media_type": "image/png", "data": "not*base64*!!"}
    with pytest.raises(VisionMessageError):
        vision._validate_source(src)


def test_rejects_unparseable_image_data():
    fake = base64.b64encode(b"not a real image").decode("ascii")
    src = {"type": "base64", "media_type": "image/png", "data": fake}
    with pytest.raises(VisionMessageError, match="无法解析"):
        vision._validate_source(src)


def test_rejects_empty_url():
    with pytest.raises(VisionMessageError, match="URL 为空"):
        vision._validate_source({"type": "url", "url": ""})


def test_accepts_valid_url_source():
    vision._validate_source({"type": "url", "url": "https://example.com/a.png"})


# ── Anthropic 序列化 ────────────────────────────────────────────────

def test_anthropic_serializes_content_blocks():
    b64 = _png_b64()
    out = vision.to_anthropic_messages([_image_message(b64)])
    content = out[0]["content"]
    assert content[0] == {"type": "text", "text": "这是什么构件？"}
    assert content[1]["type"] == "image"
    assert content[1]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": b64,
    }


def test_anthropic_does_not_mutate_input():
    original = _image_message(_png_b64())
    snapshot = original["content"][1]["source"]["data"]
    vision.to_anthropic_messages([original])
    assert original["content"][1]["source"]["data"] == snapshot


# ── OpenAI 序列化 ───────────────────────────────────────────────────

def test_openai_serializes_image_url_from_base64():
    b64 = _png_b64()
    out = vision.to_openai_messages([_image_message(b64)])
    content = out[0]["content"]
    assert content[0] == {"type": "text", "text": "这是什么构件？"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == f"data:image/png;base64,{b64}"


def test_openai_serializes_image_url_from_url():
    msg = {
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "url", "url": "https://x/a.png"}}
        ],
    }
    out = vision.to_openai_messages([msg])
    assert out[0]["content"][0]["image_url"]["url"] == "https://x/a.png"


# ── Ollama 序列化 ───────────────────────────────────────────────────

def test_ollama_serializes_images_list_and_joined_text():
    b64 = _png_b64()
    out = vision.to_ollama_messages([_image_message(b64, text="识别构件")])
    assert out[0]["content"] == "识别构件"
    assert out[0]["images"] == [b64]


def test_ollama_rejects_url_source():
    msg = {
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "url", "url": "https://x/a.png"}}
        ],
    }
    with pytest.raises(VisionMessageError, match="仅支持 base64"):
        vision.to_ollama_messages([msg])


# ── text-only 回归（转换函数保持字符串 content 原样）────────────────

@pytest.mark.parametrize(
    "convert",
    [vision.to_anthropic_messages, vision.to_openai_messages, vision.to_ollama_messages],
)
def test_text_only_content_passthrough(convert):
    msgs = [{"role": "user", "content": "纯文本问题"}]
    assert convert(msgs) == msgs


# ── Provider.complete 层：多模态 payload 实际下发 ───────────────────

@pytest.mark.asyncio
async def test_anthropic_complete_sends_content_blocks():
    provider = AnthropicProvider(api_key="k")
    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(text="梁")]
    fake_resp.usage = MagicMock(input_tokens=10, output_tokens=2)
    provider.client.messages.create = AsyncMock(return_value=fake_resp)

    b64 = _png_b64()
    await provider.complete([_image_message(b64)], _PARAMS)

    sent = provider.client.messages.create.call_args.kwargs["messages"]
    assert sent[0]["content"][1]["type"] == "image"
    assert sent[0]["content"][1]["source"]["data"] == b64


@pytest.mark.asyncio
async def test_anthropic_complete_text_only_unchanged():
    provider = AnthropicProvider(api_key="k")
    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(text="ok")]
    fake_resp.usage = MagicMock(input_tokens=1, output_tokens=1)
    provider.client.messages.create = AsyncMock(return_value=fake_resp)

    msgs = [{"role": "user", "content": "只问文字"}]
    await provider.complete(msgs, _PARAMS)

    sent = provider.client.messages.create.call_args.kwargs["messages"]
    assert sent == msgs


@pytest.mark.asyncio
async def test_openai_complete_sends_image_url():
    provider = OpenAICompatProvider(api_key="k", base_url="http://x/v1")
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="柱"))]
    fake_resp.usage = MagicMock(prompt_tokens=8, completion_tokens=1)
    provider.client.chat.completions.create = AsyncMock(return_value=fake_resp)

    b64 = _png_b64()
    await provider.complete([_image_message(b64)], _PARAMS)

    sent = provider.client.chat.completions.create.call_args.kwargs["messages"]
    assert sent[0]["content"][1]["type"] == "image_url"
    assert sent[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_ollama_complete_sends_images_field():
    provider = OllamaProvider(base_url="http://x")
    captured: dict = {}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            captured["json"] = json
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={
                    "message": {"content": "板"},
                    "prompt_eval_count": 5,
                    "eval_count": 1,
                }
            )
            return resp

    b64 = _png_b64()
    with patch("core.llm.providers.ollama_provider.httpx.AsyncClient", return_value=_FakeClient()):
        await provider.complete([_image_message(b64, text="识别")], _PARAMS)

    sent = captured["json"]["messages"]
    assert sent[0]["content"] == "识别"
    assert sent[0]["images"] == [b64]
