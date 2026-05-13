"""
数据看板 API 测试

覆盖目标:
- GET /dashboard/group — 需要 isAdmin
- GET /dashboard/project/{id} — 所有登录用户
- 权限检查：非管理员访问集团看板返回 403
- 数据结构校验：返回的字段齐全
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from core.auth import create_access_token
from main import app


# ── 通用 Mock 数据 ────────────────────────────────────────────────

GROUP_MOCK = {
    "annual_saving_yuan": 1200000,
    "proposal_funnel": [
        {"status": "approved", "cnt": 5, "total_saving": 800000},
        {"status": "paid",     "cnt": 3, "total_saving": 400000},
    ],
    "drawing_overview": {
        "total": 120,
        "ai_coverage_rate": 0.85,
        "by_status": [
            {"status": "published", "cnt": 80, "ai_done_cnt": 70},
            {"status": "draft",     "cnt": 10, "ai_done_cnt": 0},
        ],
    },
    "review_stats": {"tech_pass_rate": 0.82, "econ_sign_rate": 0.78},
    "kpi_warnings": [],
    "llm_cost_30d": [
        {"engine_name": "rag_qa", "call_count": 200, "total_cost_usd": 1.25},
    ],
    "regulation_stats": {"book_count": 15, "article_count": 3200, "vectorized_count": 3000},
    "generated_at": "2026-05-13T10:00:00+08:00",
}

PROJECT_MOCK = {
    "drawings_by_status": [
        {"status": "published", "cnt": 20},
        {"status": "draft",     "cnt": 5},
    ],
    "ai_quality": {
        "reviewed_count": 18,
        "avg_issues": 2.3,
        "total_critical": 4,
        "drawings_with_critical": 3,
    },
    "stage_distribution": [
        {"discipline": "structure", "total_cnt": 10, "published_cnt": 8, "rejected_cnt": 1},
    ],
    "proposal_funnel": [
        {"status": "approved", "cnt": 2, "total_saving": 30000},
    ],
    "annual_saving_yuan": 30000,
    "kpi_red_flag": False,
    "recent_activity": [
        {
            "action": "upload_drawing",
            "operator": "张三",
            "created_at": "2026-05-13T09:00:00+08:00",
        }
    ],
}


# ── Fixtures ──────────────────────────────────────────────────────

def _make_token(role: str) -> str:
    uid = str(uuid.uuid4())
    return create_access_token({"sub": uid, "role": role})


def _auth(role: str) -> dict:
    return {"Authorization": f"Bearer {_make_token(role)}"}


@pytest_asyncio.fixture
async def mock_client():
    from dependencies import get_db, get_current_user

    class FakeDB:
        execute = AsyncMock(return_value=None)
        fetch_one = AsyncMock(return_value=None)
        fetch_all = AsyncMock(return_value=[])

    fake_db = FakeDB()

    def _override_user(role: str):
        return lambda: {"id": str(uuid.uuid4()), "username": f"u_{role}", "role": role}

    yield fake_db, _override_user
    app.dependency_overrides.clear()


# ── 集团看板 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_group_dashboard_requires_admin():
    """非管理员访问集团看板应返回 403"""
    from dependencies import get_db, get_current_user

    class FakeDB:
        fetch_all = AsyncMock(return_value=[])
        fetch_one = AsyncMock(return_value=None)
        execute = AsyncMock()

    app.dependency_overrides[get_db] = lambda: FakeDB()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": str(uuid.uuid4()), "username": "pm", "role": "pm"
    }
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/dashboard/group")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_group_dashboard_structure():
    """集团看板应返回正确数据结构"""
    from dependencies import get_db, get_current_user
    from routers.dashboard import router as dashboard_router

    class FakeDB:
        fetch_one = AsyncMock(return_value={"annual_saving_yuan": 1200000})
        fetch_all = AsyncMock(return_value=[])
        execute = AsyncMock()

    app.dependency_overrides[get_db] = lambda: FakeDB()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": str(uuid.uuid4()), "username": "admin", "role": "group_admin"
    }
    try:
        with patch("routers.dashboard._get_group_dashboard", return_value=GROUP_MOCK):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/v1/dashboard/group")
        assert resp.status_code == 200
        data = resp.json()
        required_keys = [
            "annual_saving_yuan", "proposal_funnel", "drawing_overview",
            "review_stats", "kpi_warnings", "llm_cost_30d",
            "regulation_stats", "generated_at",
        ]
        for key in required_keys:
            assert key in data, f"缺少字段: {key}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_project_dashboard_accessible_to_all_roles():
    """项目看板允许所有已登录用户访问"""
    from dependencies import get_db, get_current_user

    project_id = str(uuid.uuid4())

    class FakeDB:
        async def fetch_one(self, *args, **kwargs):
            return {"id": project_id}

        async def fetch_all(self, *args, **kwargs):
            return []

        execute = AsyncMock()

    app.dependency_overrides[get_db] = lambda: FakeDB()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": str(uuid.uuid4()), "username": "designer", "role": "designer"
    }
    try:
        with patch("routers.dashboard._get_project_dashboard", return_value=PROJECT_MOCK):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/v1/dashboard/project/{project_id}")
        # 应返回 200（非管理员也能访问）
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_project_dashboard_structure():
    """项目看板应包含所有必要字段"""
    from dependencies import get_db, get_current_user

    project_id = str(uuid.uuid4())

    class FakeDB:
        async def fetch_one(self, *args, **kwargs):
            return {"id": project_id}

        async def fetch_all(self, *args, **kwargs):
            return []

        execute = AsyncMock()

    app.dependency_overrides[get_db] = lambda: FakeDB()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": str(uuid.uuid4()), "username": "pm", "role": "pm"
    }
    try:
        with patch("routers.dashboard._get_project_dashboard", return_value=PROJECT_MOCK):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/v1/dashboard/project/{project_id}")
        assert resp.status_code == 200
        data = resp.json()
        required_keys = [
            "drawings_by_status", "ai_quality", "stage_distribution",
            "proposal_funnel", "annual_saving_yuan", "kpi_red_flag", "recent_activity",
        ]
        for key in required_keys:
            assert key in data, f"缺少字段: {key}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_project_dashboard_kpi_red_flag_type():
    """kpi_red_flag 应为 bool 类型"""
    from dependencies import get_db, get_current_user

    project_id = str(uuid.uuid4())

    class FakeDB:
        async def fetch_one(self, *args, **kwargs):
            return {"id": project_id}
        async def fetch_all(self, *args, **kwargs):
            return []
        execute = AsyncMock()

    app.dependency_overrides[get_db] = lambda: FakeDB()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": str(uuid.uuid4()), "username": "pm", "role": "pm"
    }
    try:
        with patch("routers.dashboard._get_project_dashboard", return_value=PROJECT_MOCK):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/v1/dashboard/project/{project_id}")
        assert isinstance(resp.json()["kpi_red_flag"], bool)
    finally:
        app.dependency_overrides.clear()
