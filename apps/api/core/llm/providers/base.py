from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ModelParams:
    model_id: str
    temperature: float = 0.1
    max_tokens: int = 2048
    top_p: float = 1.0
    frequency_penalty: float = 0.0
    timeout_sec: int = 120
    extra: dict[str, Any] | None = None


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    model_id: str
    latency_ms: int = 0


def split_system_messages(
    messages: list[dict],
) -> tuple[str | None, list[dict]]:
    """把 `role: system` 的消息抽出来 → (系统提示, 其余消息)。

    统一约定：**系统提示一律写成 `{"role": "system"}` 消息**，
    由各 provider 按自家 API 转换。Anthropic 的 Messages API
    **拒绝 system 角色的消息**，必须走独立字段；
    OpenAI 兼容 / Ollama 原生接受，无需转换。

    不限位置——有的调用方会把系统提示放在后面，按位置取会漏掉，
    而漏掉的后果是 Anthropic 直接 400 拒绝整个请求。
    """
    system_parts = [
        str(m.get("content", "")) for m in messages if m.get("role") == "system"
    ]
    rest = [m for m in messages if m.get("role") != "system"]
    return ("\n\n".join(p for p in system_parts if p) or None, rest)


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, messages: list[dict], params: ModelParams) -> LLMResponse:
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        pass
