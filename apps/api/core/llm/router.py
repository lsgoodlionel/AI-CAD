"""
统一 LLM 路由器。
- 所有配置从 PostgreSQL 读取，30s 缓存热更新，无需重启
- 每个引擎有独立的主模型 / 备用链 / 批量模型
- 断路器（Redis）防止级联失败
- 调用日志异步写入，不阻塞主流程
"""
import os
import time
import asyncio
import logging
from datetime import datetime, timedelta
from uuid import UUID

from redis.asyncio import Redis

from .providers import (
    LLMProvider, LLMResponse, ModelParams,
    AnthropicProvider, OpenAICompatProvider,
    OllamaProvider, CustomHTTPProvider,
)
from .circuit_breaker import CircuitBreaker, CBState

logger = logging.getLogger(__name__)

TASK_PRIORITY = ["primary", "fallback_1", "fallback_2", "batch"]


class EngineConfig:
    def __init__(self, row: dict):
        self.engine_name: str = row["engine_name"]
        self.task_type: str = row["task_type"]
        self.model_db_id: UUID = row["model_db_id"]
        self.model_id: str = row["model_id"]          # e.g. "claude-sonnet-4-6"
        self.provider_type: str = row["provider_type"] # "anthropic"|"openai_compat"|"ollama"|"custom_http"
        self.base_url: str | None = row.get("base_url")
        self.api_key_env: str | None = row.get("api_key_env")
        self.params = ModelParams(
            model_id=row["model_id"],
            temperature=float(row.get("temperature", 0.1)),
            max_tokens=int(row.get("max_tokens", 2048)),
            top_p=float(row.get("top_p", 1.0)),
            frequency_penalty=float(row.get("frequency_penalty", 0.0)),
            timeout_sec=int(row.get("timeout_sec", 120)),
            extra=row.get("extra_params"),
        )
        self.prompt_template_version: str | None = row.get("prompt_template_version")
        self.input_price_per_1m: float = float(row.get("input_price_per_1m") or 0)
        self.output_price_per_1m: float = float(row.get("output_price_per_1m") or 0)


class ModelRouter:
    """
    用法：
        response = await router.route("kg_reasoning", messages)
        response = await router.route("regulation_classifier", messages, task_type="batch")
    """

    CACHE_TTL = timedelta(seconds=30)

    def __init__(self, db, redis: Redis):
        self._db = db
        self._redis = redis
        self._config_cache: dict[str, tuple[datetime, list[EngineConfig]]] = {}
        self._provider_cache: dict[str, LLMProvider] = {}
        self._cbs: dict[str, CircuitBreaker] = {}

    # ──────────────────────────── public ────────────────────────────

    async def route(
        self,
        engine_name: str,
        messages: list[dict],
        task_type: str = "primary",
        _fallback_depth: int = 0,
    ) -> LLMResponse:
        if _fallback_depth > 2:
            raise RuntimeError(f"[{engine_name}] 所有备用模型均不可用")

        config = await self._get_config(engine_name, task_type)
        if config is None:
            if task_type != "primary":
                raise RuntimeError(f"[{engine_name}] 无 {task_type} 配置")
            # 没有 batch/fallback 配置时，回退到 primary
            config = await self._get_config(engine_name, "primary")
            if config is None:
                raise RuntimeError(f"[{engine_name}] 未找到任何模型配置")

        cb = self._get_cb(engine_name, task_type)

        if await cb.is_open():
            logger.warning("[%s/%s] 断路器 OPEN，尝试下一备用", engine_name, task_type)
            return await self._try_next(engine_name, messages, task_type, _fallback_depth)

        provider = self._get_provider(config)
        try:
            response = await provider.complete(messages, config.params)
            await cb.record_success()
            asyncio.create_task(self._log(engine_name, config, response, success=True))
            return response

        except Exception as exc:
            logger.error("[%s/%s] 调用失败: %s", engine_name, task_type, exc)
            await cb.record_failure()
            asyncio.create_task(self._log(engine_name, config, None, success=False, error=str(exc)))
            return await self._try_next(engine_name, messages, task_type, _fallback_depth)

    async def health_status(self) -> dict[str, bool]:
        """返回所有提供商的健康状态（用于管理后台展示）"""
        providers = await self._db.fetch_all(
            "SELECT id, name, provider_type, base_url, api_key_env FROM llm_providers WHERE is_active = true"
        )
        results: dict[str, bool] = {}
        tasks = [
            (row["name"], self._check_provider_health(row))
            for row in providers
        ]
        for name, coro in tasks:
            try:
                results[name] = await coro
            except Exception:
                results[name] = False
        return results

    # ──────────────────────────── private ───────────────────────────

    async def _fetch_all(self, query: str, values: dict | None = None):
        if values and self._db.__class__.__name__ == "DatabaseAdapter":
            return await self._db.fetch_all(query, **values)
        return await self._db.fetch_all(query, values) if values else await self._db.fetch_all(query)

    async def _execute(self, query: str, values: dict | None = None):
        if values and self._db.__class__.__name__ == "DatabaseAdapter":
            return await self._db.execute(query, **values)
        return await self._db.execute(query, values) if values else await self._db.execute(query)

    async def _try_next(self, engine_name, messages, task_type, depth) -> LLMResponse:
        idx = TASK_PRIORITY.index(task_type) if task_type in TASK_PRIORITY else 0
        if idx + 1 < len(TASK_PRIORITY):
            next_type = TASK_PRIORITY[idx + 1]
            return await self.route(engine_name, messages, next_type, depth + 1)
        raise RuntimeError(f"[{engine_name}] 无可用模型")

    async def _get_config(self, engine_name: str, task_type: str) -> EngineConfig | None:
        cache_key = f"{engine_name}:{task_type}"
        cached = self._config_cache.get(cache_key)
        if cached and datetime.now() - cached[0] < self.CACHE_TTL:
            return cached[1][0] if cached[1] else None

        rows = await self._fetch_all(
            """
            SELECT emc.engine_name, emc.task_type, emc.id AS model_db_id,
                   emc.temperature, emc.max_tokens, emc.top_p,
                   emc.frequency_penalty, emc.prompt_template_version, emc.extra_params,
                   lm.model_id, lm.input_price_per_1m, lm.output_price_per_1m,
                   lp.provider_type, lp.base_url, lp.api_key_env, lp.timeout_sec
            FROM engine_model_configs emc
            JOIN llm_models lm ON emc.model_id = lm.id
            JOIN llm_providers lp ON lm.provider_id = lp.id
            WHERE emc.engine_name = :engine_name AND emc.task_type = :task_type
              AND emc.is_enabled = true AND lp.is_active = true AND lm.is_active = true
            """,
            {"engine_name": engine_name, "task_type": task_type},
        )
        configs = [EngineConfig(dict(r)) for r in rows]
        self._config_cache[cache_key] = (datetime.now(), configs)
        return configs[0] if configs else None

    def _get_provider(self, config: EngineConfig) -> LLMProvider:
        api_key = os.environ.get(config.api_key_env or "", "")
        cache_key = f"{config.provider_type}:{config.base_url}:{api_key[:8]}"
        if cache_key not in self._provider_cache:
            self._provider_cache[cache_key] = self._build_provider(config, api_key)
        return self._provider_cache[cache_key]

    @staticmethod
    def _build_provider(config: EngineConfig, api_key: str) -> LLMProvider:
        match config.provider_type:
            case "anthropic":
                return AnthropicProvider(api_key=api_key, base_url=config.base_url)
            case "openai_compat":
                return OpenAICompatProvider(api_key=api_key, base_url=config.base_url or "https://api.openai.com/v1")
            case "ollama":
                return OllamaProvider(base_url=config.base_url or "http://host.docker.internal:11434")
            case "custom_http":
                extra = config.params.extra or {}
                return CustomHTTPProvider(
                    base_url=config.base_url or "",
                    api_key=api_key,
                    request_template=extra.get("request_template", {}),
                    response_content_path=extra.get("response_content_path", "choices.0.message.content"),
                )
            case _:
                raise ValueError(f"未知 provider_type: {config.provider_type}")

    def _get_cb(self, engine: str, task_type: str) -> CircuitBreaker:
        key = f"{engine}:{task_type}"
        if key not in self._cbs:
            self._cbs[key] = CircuitBreaker(self._redis, key)
        return self._cbs[key]

    async def _log(
        self,
        engine_name: str,
        config: EngineConfig,
        response: LLMResponse | None,
        success: bool,
        error: str | None = None,
    ):
        cost = 0.0
        if response:
            cost = (
                response.prompt_tokens / 1_000_000 * config.input_price_per_1m +
                response.completion_tokens / 1_000_000 * config.output_price_per_1m
            )
        # 调用日志属可观测性、非关键路径：写入失败（如缺分区、DB 抖动）只告警，
        # 绝不上抛打断 LLM 调用链路（审图/KG/RAG 依赖）。DEFAULT 分区兜底见 migration 028。
        try:
            await self._execute(
                """
                INSERT INTO llm_call_logs
                  (engine_name, model_db_id, prompt_tokens, completion_tokens,
                   latency_ms, cost_usd, success, error_type)
                VALUES (:engine_name,:model_db_id,:prompt_tokens,:completion_tokens,
                        :latency_ms,:cost_usd,:success,:error_type)
                """,
                {
                    "engine_name": engine_name,
                    "model_db_id": config.model_db_id,
                    "prompt_tokens": response.prompt_tokens if response else 0,
                    "completion_tokens": response.completion_tokens if response else 0,
                    "latency_ms": response.latency_ms if response else 0,
                    "cost_usd": cost,
                    "success": success,
                    "error_type": error[:200] if error else None,
                },
            )
        except Exception as exc:  # noqa: BLE001 — 日志写入失败不得阻断调用
            logger.warning("[%s] 调用日志写入失败（已忽略，不影响调用）: %s", engine_name, exc)

    async def _check_provider_health(self, row: dict) -> bool:
        api_key = os.environ.get(row.get("api_key_env") or "", "")
        config = EngineConfig({
            "engine_name": "_health", "task_type": "primary",
            "model_db_id": None, "model_id": "health-check",
            "provider_type": row["provider_type"],
            "base_url": row.get("base_url"),
            "api_key_env": row.get("api_key_env"),
            "temperature": 0.0, "max_tokens": 1,
        })
        provider = self._get_provider(config)
        return await provider.health_check()

    def invalidate_cache(self, engine_name: str | None = None):
        """管理后台变更配置后调用，立即失效缓存"""
        if engine_name:
            self._config_cache = {
                k: v for k, v in self._config_cache.items()
                if not k.startswith(engine_name)
            }
            self._provider_cache.clear()
        else:
            self._config_cache.clear()
            self._provider_cache.clear()
