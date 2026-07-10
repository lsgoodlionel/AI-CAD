"""本地 Ollama 提供商：qwen2.5:72b / llama3.3 / deepseek-r1:14b 等"""
import time
import httpx
from .base import LLMProvider, LLMResponse, ModelParams
from . import vision


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str = "http://host.docker.internal:11434"):
        self.base_url = base_url.rstrip("/")

    async def complete(self, messages: list[dict], params: ModelParams) -> LLMResponse:
        # 仅当含图像块时才走多模态转换，text-only 保持零差异
        if vision.messages_have_images(messages):
            messages = vision.to_ollama_messages(messages)
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=params.timeout_sec) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": params.model_id,
                    "messages": messages,
                    "options": {
                        "temperature": params.temperature,
                        "top_p": params.top_p,
                        "num_predict": params.max_tokens,
                    },
                    "stream": False,
                },
            )
            response.raise_for_status()
            data = response.json()

        latency = int((time.monotonic() - start) * 1000)
        return LLMResponse(
            content=data["message"]["content"],
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            model_id=params.model_id,
            latency_ms=latency,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
        return data.get("models", [])
