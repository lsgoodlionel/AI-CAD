import time
import anthropic
from .base import LLMProvider, LLMResponse, ModelParams


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str | None = None):
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.AsyncAnthropic(**kwargs)

    async def complete(self, messages: list[dict], params: ModelParams) -> LLMResponse:
        start = time.monotonic()
        response = await self.client.messages.create(
            model=params.model_id,
            max_tokens=params.max_tokens,
            temperature=params.temperature,
            top_p=params.top_p,
            messages=messages,
        )
        latency = int((time.monotonic() - start) * 1000)
        return LLMResponse(
            content=response.content[0].text,
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            model_id=params.model_id,
            latency_ms=latency,
        )

    async def health_check(self) -> bool:
        try:
            await self.client.models.list()
            return True
        except Exception:
            return False
