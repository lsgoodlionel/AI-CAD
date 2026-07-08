"""FastAPI 全局依赖注入"""
import re

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import redis.asyncio as aioredis

from core.auth import decode_token
from core.config import settings
from core.database import database
from core.llm.router import ModelRouter

_bearer = HTTPBearer()

# ── 数据库 ───────────────────────────────────────────────────

class DatabaseAdapter:
    def __init__(self, db):
        self._db = db

    @staticmethod
    def _normalize(query: str, args: tuple, kwargs: dict):
        if kwargs:
            return query, kwargs
        if not args:
            return query, None
        if len(args) == 1 and isinstance(args[0], dict):
            return query, args[0]
        values = {f"p{i}": value for i, value in enumerate(args, start=1)}
        normalized = re.sub(r"\$(\d+)", lambda m: f":p{m.group(1)}", query)
        return normalized, values

    async def fetch_one(self, query: str, *args, **kwargs):
        query, values = self._normalize(query, args, kwargs)
        return await self._db.fetch_one(query, values)

    async def fetch_all(self, query: str, *args, **kwargs):
        query, values = self._normalize(query, args, kwargs)
        return await self._db.fetch_all(query, values)

    async def fetch_val(self, query: str, *args, **kwargs):
        row = await self.fetch_one(query, *args, **kwargs)
        if row is None:
            return None
        return next(iter(row.values()))

    async def execute(self, query: str, *args, **kwargs):
        query, values = self._normalize(query, args, kwargs)
        return await self._db.execute(query, values)


_database_adapter = DatabaseAdapter(database)


async def get_db():
    return _database_adapter


# ── Redis ────────────────────────────────────────────────────

_redis_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_pool


# ── 模型路由器 ────────────────────────────────────────────────

_router_instance: ModelRouter | None = None


async def get_router(
    db=Depends(get_db),
    redis=Depends(get_redis),
) -> ModelRouter:
    global _router_instance
    if _router_instance is None:
        _router_instance = ModelRouter(db=db, redis=redis)
    return _router_instance


# ── JWT 认证 ──────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db=Depends(get_db),
):
    try:
        payload = decode_token(credentials.credentials)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或已过期的 Token",
        )
    user = await db.fetch_one(
        "SELECT id, username, role, is_active FROM users WHERE id=$1",
        payload["sub"],
    )
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不存在或已停用")
    return dict(user)


async def require_admin(user: dict = Depends(get_current_user)):
    if user["role"] != "group_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要集团管理员权限",
        )
    return user


async def require_economist(user: dict = Depends(get_current_user)):
    if user["role"] not in ("economist", "group_admin", "group_commercial_director"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要经济师权限",
        )
    return user
