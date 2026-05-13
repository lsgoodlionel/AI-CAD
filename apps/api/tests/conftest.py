"""
pytest 全局 fixtures

- fake_db: 异步 Mock DB（不需要真实 PostgreSQL）
- auth_headers: 生成各角色 JWT Bearer token
- override_deps: 依赖注入覆盖（get_db / get_current_user）
- async_client: httpx.AsyncClient 连接 FastAPI app
"""
import uuid
from unittest.mock import AsyncMock, MagicMock
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from core.auth import create_access_token
from main import app


# ── 假 DB ────────────────────────────────────────────────────────

class FakeDB:
    """轻量 Mock，覆盖 execute / fetch_one / fetch_all"""

    def __init__(self):
        self.execute = AsyncMock(return_value=None)
        self.fetch_one = AsyncMock(return_value=None)
        self.fetch_all = AsyncMock(return_value=[])

    def reset(self):
        self.execute.reset_mock()
        self.fetch_one.reset_mock()
        self.fetch_all.reset_mock()


@pytest.fixture
def fake_db():
    return FakeDB()


# ── 用户 Fixtures ─────────────────────────────────────────────────

def _make_user(role: str, uid: str | None = None) -> dict:
    uid = uid or str(uuid.uuid4())
    return {
        "id": uid,
        "username": f"test_{role}",
        "email": f"{role}@test.local",
        "role": role,
        "is_active": True,
    }


@pytest.fixture
def admin_user():
    return _make_user("group_admin")


@pytest.fixture
def pm_user():
    return _make_user("pm")


@pytest.fixture
def economist_user():
    return _make_user("economist")


@pytest.fixture
def designer_user():
    return _make_user("designer")


def _bearer(user: dict) -> dict:
    token = create_access_token({"sub": user["id"], "role": user["role"]})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(admin_user):
    return _bearer(admin_user)


@pytest.fixture
def pm_headers(pm_user):
    return _bearer(pm_user)


@pytest.fixture
def economist_headers(economist_user):
    return _bearer(economist_user)


# ── AsyncClient ──────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(fake_db, admin_user) -> AsyncGenerator[AsyncClient, None]:
    from dependencies import get_db, get_current_user

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_user] = lambda: admin_user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_as(fake_db):
    """动态角色版本：client_as(user_dict) 返回 AsyncClient"""
    from dependencies import get_db, get_current_user

    def _make_client(user: dict):
        app.dependency_overrides[get_db] = lambda: fake_db
        app.dependency_overrides[get_current_user] = lambda: user
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _make_client
    app.dependency_overrides.clear()
