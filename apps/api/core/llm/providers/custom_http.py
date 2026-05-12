"""
自研模型 / 专业审图模型，适配任意 HTTP REST 接口。
request_template 和 response_path 在管理后台配置，无需改代码。
"""
import time
import json
from string import Template
import httpx
from .base import LLMProvider, LLMResponse, ModelParams


class CustomHTTPProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        request_template: dict,   # Jinja/Template 格式，管理后台配置
        response_content_path: str,   # JSONPath，如 "choices.0.message.content"
        response_prompt_tokens_path: str = "usage.prompt_tokens",
        response_completion_tokens_path: str = "usage.completion_tokens",
        extra_headers: dict | None = None,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.request_template = request_template
        self.response_content_path = response_content_path
        self.response_prompt_tokens_path = response_prompt_tokens_path
        self.response_completion_tokens_path = response_completion_tokens_path
        self.extra_headers = extra_headers or {}

    async def complete(self, messages: list[dict], params: ModelParams) -> LLMResponse:
        payload = self._render_request(messages, params)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=params.timeout_sec) as client:
            response = await client.post(self.base_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        latency = int((time.monotonic() - start) * 1000)
        return LLMResponse(
            content=self._extract(data, self.response_content_path),
            prompt_tokens=int(self._extract(data, self.response_prompt_tokens_path) or 0),
            completion_tokens=int(self._extract(data, self.response_completion_tokens_path) or 0),
            model_id=params.model_id,
            latency_ms=latency,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(self.base_url.rsplit("/", 1)[0] + "/health")
                return r.status_code < 500
        except Exception:
            return False

    def _render_request(self, messages: list[dict], params: ModelParams) -> dict:
        raw = json.dumps(self.request_template)
        rendered = Template(raw).safe_substitute(
            model=params.model_id,
            temperature=params.temperature,
            max_tokens=params.max_tokens,
            messages=json.dumps(messages),
        )
        return json.loads(rendered)

    @staticmethod
    def _extract(data: dict, path: str) -> str | None:
        parts = path.split(".")
        cur: dict | list = data
        for p in parts:
            if cur is None:
                return None
            if isinstance(cur, list):
                cur = cur[int(p)]
            else:
                cur = cur.get(p)
        return cur
