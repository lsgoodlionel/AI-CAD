"""Router/task integration tests for the second coverage tranche."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.auth import hash_password


def _row(**values):
    return values


@pytest.mark.asyncio
async def test_engine_config_duplicate_returns_conflict(client, fake_db):
    fake_db.fetch_one.return_value = _row(id="existing-config")

    resp = await client.post(
        "/api/v1/admin/llm/engine-configs",
        json={
            "engine_name": "kg_diff_analyzer",
            "task_type": "primary",
            "model_id": "232bd54f-d7d0-41c0-8541-a070463895cc",
            "temperature": 0.05,
            "max_tokens": 6144,
            "top_p": 1,
            "frequency_penalty": 0,
        },
    )

    assert resp.status_code == 409
    assert "配置已存在" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_auth_login_success_updates_last_login(client, fake_db):
    user_id = "11111111-1111-1111-1111-111111111111"
    fake_db.fetch_one.return_value = _row(
        id=user_id,
        hashed_password=hash_password("secret123"),
        role="group_admin",
        display_name="系统管理员",
        is_active=True,
    )

    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "secret123"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert data["user"]["role"] == "group_admin"
    fake_db.execute.assert_awaited()


@pytest.mark.asyncio
async def test_auth_login_rejects_bad_password(client, fake_db):
    fake_db.fetch_one.return_value = _row(
        id="u1",
        hashed_password=hash_password("right-password"),
        role="designer",
        display_name="设计人员",
        is_active=True,
    )

    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "designer", "password": "wrong-password"},
    )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_drawings_list_detail_and_ai_issues(client, fake_db):
    drawing_id = "77777777-7777-7777-7777-777777777777"
    report_id = "88888888-8888-8888-8888-888888888888"
    now = datetime.now(timezone.utc)

    fake_db.fetch_all.side_effect = [
        [
            _row(
                id=drawing_id,
                drawing_no="E2E-ARCH-001",
                title="E2E 建筑深化样图",
                discipline="architecture",
                version="A",
                status="ai_done",
                current_stage="technical_review",
                estimated_impact=650000,
                created_at=now,
                updated_at=now,
                creator_name="深化设计师",
                project_name="E2E示范项目",
            )
        ],
        [
            _row(
                id="issue-1",
                engine="rule",
                severity="critical",
                category="强制性条文",
                description="疏散宽度不满足规范要求",
                regulation_ref="GB50016-2014 5.5.18",
                location_x=0.35,
                location_y=0.42,
                suggestion="复核疏散宽度并调整墙线",
                status="open",
                closed_at=None,
                created_at=now,
            )
        ],
    ]
    fake_db.fetch_val.side_effect = [1, 1]
    fake_db.fetch_one.side_effect = [
        _row(
            id=drawing_id,
            drawing_no="E2E-ARCH-001",
            title="E2E 建筑深化样图",
            discipline="architecture",
            version="A",
            status="ai_done",
            current_stage="technical_review",
            file_key="projects/e2e.pdf",
            created_at=now,
            updated_at=now,
            creator_name="深化设计师",
            project_name="E2E示范项目",
            project_id="p1",
        ),
        _row(id=report_id, status="done", total_issues=1, critical_issues=1, completed_at=now),
        _row(
            id=report_id,
            drawing_id=drawing_id,
            status="done",
            total_issues=1,
            critical_issues=1,
            completed_at=now,
        ),
    ]

    list_resp = await client.get("/api/v1/drawings")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    detail_resp = await client.get(f"/api/v1/drawings/{drawing_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["ai_report"]["status"] == "done"

    issues_resp = await client.get(f"/api/v1/drawings/{drawing_id}/ai-review/issues")
    assert issues_resp.status_code == 200
    assert issues_resp.json()["items"][0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_drawings_upload_persists_and_dispatches_review(client, fake_db):
    fake_db.fetch_one.side_effect = [
        _row(id="77777777-7777-7777-7777-777777777777"),
        _row(id="88888888-8888-8888-8888-888888888888"),
    ]
    delayed = MagicMock()

    with (
        patch("routers.drawings.upload_file") as upload_file,
        patch("routers.drawings.run_ai_review.delay", delayed),
        patch("routers.drawings.write_audit", new=AsyncMock()),
    ):
        resp = await client.post(
            "/api/v1/drawings",
            data={
                "project_id": "22222222-2222-2222-2222-222222222222",
                "drawing_no": "E2E-UPLOAD-001",
                "discipline": "architecture",
                "version": "A",
                "title": "上传样图",
                "estimated_impact": "650000",
            },
            files={"file": ("drawing.pdf", b"%PDF-1.4\n", "application/pdf")},
        )

    assert resp.status_code == 201
    assert resp.json()["status"] == "ai_reviewing"
    upload_file.assert_called_once()
    delayed.assert_called_once_with("77777777-7777-7777-7777-777777777777")


@pytest.mark.asyncio
async def test_technical_review_approves_when_gates_are_confirmed(client_as, fake_db, admin_user):
    fake_db.fetch_one.return_value = _row(
        id="d1",
        status="technical_review",
        drawing_no="E2E-ARCH-001",
        project_id="p1",
    )
    fake_db.fetch_val.return_value = 0

    with (
        patch("routers.technical_review.write_audit", new=AsyncMock()),
        patch("routers.technical_review.notify_review_task", new=AsyncMock()),
    ):
        async with client_as(admin_user) as ac:
            resp = await ac.post(
                "/api/v1/drawings/d1/technical-review",
                json={
                    "result": "approved",
                    "ai_report_confirmed": True,
                    "bim_check_confirmed": True,
                    "issues_all_closed": True,
                    "notes": "ok",
                },
            )

    assert resp.status_code == 201
    assert resp.json()["new_status"] == "economic_review"


@pytest.mark.asyncio
async def test_technical_review_blocks_unresolved_major_issues(client_as, fake_db, admin_user):
    fake_db.fetch_one.return_value = _row(
        id="d1",
        status="technical_review",
        drawing_no="E2E-ARCH-001",
        project_id="p1",
    )
    fake_db.fetch_val.return_value = 2

    async with client_as(admin_user) as ac:
        resp = await ac.post(
            "/api/v1/drawings/d1/technical-review",
            json={
                "result": "approved",
                "ai_report_confirmed": True,
                "bim_check_confirmed": True,
                "issues_all_closed": False,
            },
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_incentive_calculate_requires_economist(client_as, fake_db, economist_user):
    fake_db.fetch_one.return_value = _row(
        id="p1",
        status="draft",
        project_id="project-1",
        title="E2E 钢筋翻样优化",
    )

    with (
        patch("routers.incentive.write_audit", new=AsyncMock()),
        patch("routers.incentive.notify_proposal_sign_required", new=AsyncMock()),
    ):
        async with client_as(economist_user) as ac:
            resp = await ac.post(
                "/api/v1/incentive/proposals/p1/calculate",
                json={"net_saving": 150000, "bonus_rate": 0.15, "notes": "核算通过"},
            )

    assert resp.status_code == 200
    assert resp.json()["snapshot"]["bonus_pool"] == 22500.0


@pytest.mark.asyncio
async def test_economic_calc_router_saves_result(client, fake_db):
    fake_db.fetch_one.return_value = _row(
        id="d1",
        drawing_no="E2E-ARCH-001",
        discipline="architecture",
    )
    fake_db.fetch_all.return_value = []

    resp = await client.post(
        "/api/v1/drawings/d1/economic-calc",
        json={
            "concrete_grade": "C30",
            "seismic_grade": 2,
            "steel_price_per_ton": 4500,
            "bars": [
                {"diameter": 16, "steel_grade": "HRB400", "required_length": 3200, "count": 12},
                {"diameter": 16, "steel_grade": "HRB400", "required_length": 2800, "count": 8},
            ],
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["drawing_id"] == "d1"
    assert data["anchor_lengths"]
    fake_db.execute.assert_awaited()


@pytest.mark.asyncio
async def test_advance_expired_notices_updates_and_audits():
    from tasks import proposal_notice

    class FakeDatabase:
        def __init__(self, *_args, **_kwargs):
            self.fetch_all = AsyncMock(return_value=[_row(id="p1", title="到期提案")])
            self.execute = AsyncMock()
            self.connect = AsyncMock()
            self.disconnect = AsyncMock()

    fake_db = FakeDatabase()

    with (
        patch("tasks.proposal_notice.databases.Database", return_value=fake_db),
        patch("tasks.proposal_notice.write_audit", new=AsyncMock()) as audit,
    ):
        result = await proposal_notice._do_advance()

    assert result == {"advanced": 1}
    fake_db.execute.assert_awaited_once()
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_regulation_sync_upserts_articles_and_marks_source():
    from tasks import regulation_api_sync

    class FakeDB:
        def __init__(self):
            self.fetch_one = AsyncMock(side_effect=[None, _row(id="book-1")])
            self.execute = AsyncMock()

    fake_db = FakeDB()
    source = {
        "id": "source-1",
        "name": "地方规范 API",
        "endpoint_url": "https://example.test/regulations",
        "auth_type": "none",
        "auth_config": {"response_path": "items"},
    }

    with (
        patch(
            "tasks.regulation_api_sync._fetch_remote_articles",
            new=AsyncMock(return_value=[
                {
                    "article_no": "3.1.2",
                    "title": "安全出口",
                    "content": "安全出口数量不应少于两个。",
                    "obligation_level": "MUST",
                    "is_mandatory": True,
                    "chapter_no": "3",
                }
            ]),
        ),
        patch("tasks.regulation_api_sync.write_audit", new=AsyncMock()),
        patch("tasks.regulation_api_sync._trigger_batch_vectorize.apply_async") as vectorize,
    ):
        result = await regulation_api_sync._do_sync(fake_db, source)

    assert result == {"upserted": 1}
    assert fake_db.execute.await_count >= 2
    vectorize.assert_called_once_with(kwargs={"book_id": "book-1"})
