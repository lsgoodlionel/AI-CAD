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


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, messages: list[dict], params: ModelParams) -> LLMResponse:
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        pass
