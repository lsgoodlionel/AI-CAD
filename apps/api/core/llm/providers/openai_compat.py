"""
兼容 OpenAI Chat Completions 接口的所有提供商：
OpenAI / DeepSeek / Qwen / 月之暗面 / 零一万物 / Mistral 等
"""
import time
import openai
from .base import LLMProvider, LLMResponse, ModelParams
from . import vision


class OpenAICompatProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str):
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(self, messages: list[dict], params: ModelParams) -> LLMResponse:
        # 仅当含图像块时才走多模态转换，text-only 保持零差异
        if vision.messages_have_images(messages):
            messages = vision.to_openai_messages(messages)
        start = time.monotonic()
        kwargs: dict = {
            "model": params.model_id,
            "max_tokens": params.max_tokens,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "messages": messages,
        }
        if params.frequency_penalty:
            kwargs["frequency_penalty"] = params.frequency_penalty
        if params.extra:
            kwargs.update(params.extra)

        response = await self.client.chat.completions.create(**kwargs)
        latency = int((time.monotonic() - start) * 1000)
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            model_id=params.model_id,
            latency_ms=latency,
        )

    async def health_check(self) -> bool:
        try:
            await self.client.models.list()
            return True
        except Exception:
            return False
