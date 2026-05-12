"""
基于 Redis 的分布式断路器。
多个 Worker 共享同一个断路器状态，避免各自独立计数导致的重复失败请求。

状态机：
  CLOSED  → 正常
  OPEN    → 故障，直接拒绝请求，等待 recovery_sec 后转 HALF_OPEN
  HALF_OPEN → 允许一个探测请求，成功转 CLOSED，失败重回 OPEN
"""
import time
import json
from enum import Enum
from redis.asyncio import Redis


class CBState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        redis: Redis,
        name: str,
        failure_threshold: int = 5,      # 连续失败 N 次后断开
        success_threshold: int = 2,      # HALF_OPEN 连续成功 N 次后恢复
        recovery_sec: int = 60,          # OPEN 持续时间（秒）
    ):
        self.redis = redis
        self.key = f"cb:{name}"
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.recovery_sec = recovery_sec

    async def state(self) -> CBState:
        raw = await self.redis.get(self.key)
        if not raw:
            return CBState.CLOSED
        data = json.loads(raw)
        if data["state"] == CBState.OPEN:
            if time.time() - data["opened_at"] > self.recovery_sec:
                await self._set(CBState.HALF_OPEN, data)
                return CBState.HALF_OPEN
        return CBState(data["state"])

    async def record_success(self):
        raw = await self.redis.get(self.key)
        data = json.loads(raw) if raw else self._default()
        data["failures"] = 0
        if data["state"] == CBState.HALF_OPEN:
            data["successes"] = data.get("successes", 0) + 1
            if data["successes"] >= self.success_threshold:
                data["state"] = CBState.CLOSED
                data["successes"] = 0
        await self._save(data)

    async def record_failure(self):
        raw = await self.redis.get(self.key)
        data = json.loads(raw) if raw else self._default()
        data["failures"] = data.get("failures", 0) + 1
        data["successes"] = 0
        if data["failures"] >= self.failure_threshold:
            data["state"] = CBState.OPEN
            data["opened_at"] = time.time()
        await self._save(data)

    async def is_open(self) -> bool:
        return await self.state() == CBState.OPEN

    async def _set(self, state: CBState, data: dict):
        data["state"] = state
        await self._save(data)

    async def _save(self, data: dict):
        await self.redis.set(self.key, json.dumps(data), ex=86400)

    @staticmethod
    def _default() -> dict:
        return {"state": CBState.CLOSED, "failures": 0, "successes": 0}
