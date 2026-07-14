"""D-08 事件编排层测试：core/pipeline/{config,events,handlers} + tasks/pipeline + routers/pipeline。

DB/Celery 全部 mock（不需要真实 PostgreSQL/Redis），风格对齐
tests/test_router_task_integration.py 里 FakeDatabase + patch("...databases.Database")
的既有模式。
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from core.pipeline import config, events, handlers


def _row(**values):
    return dict(values)


# ================================================================
# core/pipeline/config.py
# ================================================================

class TestConfig:
    @pytest.mark.asyncio
    async def test_is_step_enabled_defaults_true_when_no_config(self, fake_db):
        fake_db.fetch_one.return_value = None

        result = await config.is_step_enabled(fake_db, "proj-1", config.STEP_AI_REVIEW_TO_REBUILD_SUGGESTION)

        assert result is True

    @pytest.mark.asyncio
    async def test_is_step_enabled_project_override_wins(self, fake_db):
        fake_db.fetch_one.return_value = _row(param_value=False)

        result = await config.is_step_enabled(fake_db, "proj-1", config.STEP_AI_REVIEW_TO_REBUILD_SUGGESTION)

        assert result is False
        # 只查了一次（项目级键命中，未再查全局键）
        assert fake_db.fetch_one.await_count == 1

    @pytest.mark.asyncio
    async def test_is_step_enabled_falls_back_to_global_when_no_project_override(self, fake_db):
        fake_db.fetch_one.side_effect = [None, _row(param_value=False)]

        result = await config.is_step_enabled(fake_db, "proj-1", config.STEP_MODEL_BUILT_TO_PROPOSAL_SUGGESTION)

        assert result is False
        assert fake_db.fetch_one.await_count == 2

    @pytest.mark.asyncio
    async def test_fetch_param_swallows_db_errors_and_step_stays_enabled(self, fake_db):
        fake_db.fetch_one.side_effect = RuntimeError("db down")

        result = await config.is_step_enabled(fake_db, "proj-1", config.STEP_AI_REVIEW_TO_REBUILD_SUGGESTION)

        assert result is True

    @pytest.mark.asyncio
    async def test_get_rebuild_impact_min_drawings_default(self, fake_db):
        fake_db.fetch_one.return_value = None

        assert await config.get_rebuild_impact_min_drawings(fake_db) == config.DEFAULT_REBUILD_IMPACT_MIN_DRAWINGS

    @pytest.mark.asyncio
    async def test_get_rebuild_impact_min_drawings_overridden(self, fake_db):
        fake_db.fetch_one.return_value = _row(param_value=3)

        assert await config.get_rebuild_impact_min_drawings(fake_db) == 3

    @pytest.mark.asyncio
    async def test_get_steel_price_per_ton_default(self, fake_db):
        fake_db.fetch_one.return_value = None

        assert await config.get_steel_price_per_ton(fake_db) == config.DEFAULT_STEEL_PRICE_PER_TON

    @pytest.mark.asyncio
    async def test_get_qto_saving_threshold_yuan_reads_json_string_value(self, fake_db):
        fake_db.fetch_one.return_value = _row(param_value="8000")

        assert await config.get_qto_saving_threshold_yuan(fake_db) == 8000.0


# ================================================================
# core/pipeline/events.py
# ================================================================

class TestEvents:
    @pytest.mark.asyncio
    async def test_emit_event_inserts_and_dispatches(self, fake_db):
        fake_db.fetch_one.return_value = _row(id="evt-1")

        with patch("tasks.pipeline.process_pipeline_event") as task:
            task.delay = MagicMock()
            event_id = await events.emit_event(
                fake_db,
                event_type=events.EVENT_AI_REVIEW_COMPLETED,
                project_id="proj-1",
                source_id="drawing-1",
                payload={"total_issues": 3},
            )

        assert event_id == "evt-1"
        fake_db.fetch_one.assert_awaited_once()
        task.delay.assert_called_once_with("evt-1")

    @pytest.mark.asyncio
    async def test_emit_event_dispatch_false_skips_delay(self, fake_db):
        fake_db.fetch_one.return_value = _row(id="evt-2")

        with patch("tasks.pipeline.process_pipeline_event") as task:
            task.delay = MagicMock()
            event_id = await events.emit_event(
                fake_db,
                event_type=events.EVENT_MODEL_BUILT,
                project_id="proj-1",
                dispatch=False,
            )

        assert event_id == "evt-2"
        task.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_emit_event_returns_none_when_insert_fails(self, fake_db):
        fake_db.fetch_one.side_effect = RuntimeError("insert failed")

        event_id = await events.emit_event(
            fake_db, event_type=events.EVENT_AI_REVIEW_COMPLETED, project_id="proj-1",
        )

        assert event_id is None

    @pytest.mark.asyncio
    async def test_emit_event_swallows_dispatch_failure(self, fake_db):
        fake_db.fetch_one.return_value = _row(id="evt-3")

        with patch("tasks.pipeline.process_pipeline_event") as task:
            task.delay = MagicMock(side_effect=RuntimeError("broker down"))
            event_id = await events.emit_event(
                fake_db, event_type=events.EVENT_MODEL_BUILT, project_id="proj-1",
            )

        # 派发失败不影响事件已落库的返回值
        assert event_id == "evt-3"

    @pytest.mark.asyncio
    async def test_mark_event_status_executes_update(self, fake_db):
        await events.mark_event_status(fake_db, "evt-1", "done")

        fake_db.execute.assert_awaited_once()
        args = fake_db.execute.call_args
        assert args[0][1]["status"] == "done"
        assert args[0][1]["error"] is None


# ================================================================
# core/pipeline/handlers.py
# ================================================================

class TestHandleAiReviewCompleted:
    @pytest.mark.asyncio
    async def test_skips_when_step_disabled(self, fake_db):
        with patch("core.pipeline.handlers.config.is_step_enabled", new=AsyncMock(return_value=False)):
            result = await handlers.handle_ai_review_completed(fake_db, {"id": "evt-1", "project_id": "proj-1"})

        assert result == {"skipped": "disabled"}
        fake_db.fetch_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_suggestion_when_below_threshold(self, fake_db):
        fake_db.fetch_one.return_value = None  # 模型尚未建过
        fake_db.fetch_all.return_value = [_row(id="d1", drawing_no="A-01")]

        with (
            patch("core.pipeline.handlers.config.is_step_enabled", new=AsyncMock(return_value=True)),
            patch("core.pipeline.handlers.config.get_rebuild_impact_min_drawings", new=AsyncMock(return_value=5)),
        ):
            result = await handlers.handle_ai_review_completed(fake_db, {"id": "evt-1", "project_id": "proj-1"})

        assert result["suggested"] is False
        assert result["changed_drawing_count"] == 1

    @pytest.mark.asyncio
    async def test_creates_suggestion_when_over_threshold(self, fake_db):
        fake_db.fetch_one.side_effect = [None, _row(id="sugg-1")]
        fake_db.fetch_all.return_value = [
            _row(id="d1", drawing_no="A-01"),
            _row(id="d2", drawing_no="A-02"),
        ]

        with (
            patch("core.pipeline.handlers.config.is_step_enabled", new=AsyncMock(return_value=True)),
            patch("core.pipeline.handlers.config.get_rebuild_impact_min_drawings", new=AsyncMock(return_value=1)),
        ):
            result = await handlers.handle_ai_review_completed(fake_db, {"id": "evt-1", "project_id": "proj-1"})

        assert result["suggested"] is True
        assert result["suggestion_id"] == "sugg-1"
        assert result["changed_drawing_count"] == 2
        assert result["model_ever_built"] is False


class TestHandleModelBuilt:
    @pytest.mark.asyncio
    async def test_skips_when_no_scene(self, fake_db):
        fake_db.fetch_one.return_value = _row(scene=None)

        result = await handlers.handle_model_built(fake_db, {"id": "evt-1", "project_id": "proj-1"})

        assert result == {"skipped": "no_scene"}

    @pytest.mark.asyncio
    async def test_refreshes_qto_without_suggestion_when_no_previous_summary(self, fake_db):
        scene = {"floors": []}
        fake_db.fetch_one.return_value = _row(scene=json.dumps(scene))

        with (
            patch(
                "core.pipeline.handlers.model_qto_summary.fetch_quantity_summary",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "core.pipeline.handlers.model_qto_summary.build_scene_quantities",
                return_value={"project": {"rebar": {"total_kg": 4000.0}}},
            ),
            patch(
                "core.pipeline.handlers.model_qto_summary.save_quantity_summary",
                new=AsyncMock(),
            ) as save_mock,
            patch("core.pipeline.handlers.config.is_step_enabled", new=AsyncMock(return_value=True)),
        ):
            result = await handlers.handle_model_built(fake_db, {"id": "evt-1", "project_id": "proj-1"})

        save_mock.assert_awaited_once()
        assert result["qto_refreshed"] is True
        assert result["suggested"] is False
        assert result["estimated_saving_yuan"] is None

    @pytest.mark.asyncio
    async def test_creates_proposal_suggestion_when_saving_over_threshold(self, fake_db):
        scene = {"floors": []}
        fake_db.fetch_one.side_effect = [_row(scene=json.dumps(scene)), _row(id="sugg-2")]

        with (
            patch(
                "core.pipeline.handlers.model_qto_summary.fetch_quantity_summary",
                new=AsyncMock(return_value={"payload": {"rebar": {"total_kg": 10000.0}}}),
            ),
            patch(
                "core.pipeline.handlers.model_qto_summary.build_scene_quantities",
                return_value={"project": {"rebar": {"total_kg": 4000.0}}},
            ),
            patch(
                "core.pipeline.handlers.model_qto_summary.save_quantity_summary",
                new=AsyncMock(),
            ),
            patch("core.pipeline.handlers.config.is_step_enabled", new=AsyncMock(return_value=True)),
            patch("core.pipeline.handlers.config.get_steel_price_per_ton", new=AsyncMock(return_value=4500.0)),
            patch("core.pipeline.handlers.config.get_qto_saving_threshold_yuan", new=AsyncMock(return_value=5000.0)),
        ):
            result = await handlers.handle_model_built(fake_db, {"id": "evt-1", "project_id": "proj-1"})

        # (10000 - 4000) / 1000 * 4500 = 27000.0 > 5000 门槛
        assert result["suggested"] is True
        assert result["suggestion_id"] == "sugg-2"
        assert result["estimated_saving_yuan"] == 27000.0

    @pytest.mark.asyncio
    async def test_qto_refresh_always_runs_even_when_suggestion_step_disabled(self, fake_db):
        scene = {"floors": []}
        fake_db.fetch_one.return_value = _row(scene=json.dumps(scene))

        with (
            patch(
                "core.pipeline.handlers.model_qto_summary.fetch_quantity_summary",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "core.pipeline.handlers.model_qto_summary.build_scene_quantities",
                return_value={"project": {"rebar": {"total_kg": 4000.0}}},
            ),
            patch(
                "core.pipeline.handlers.model_qto_summary.save_quantity_summary",
                new=AsyncMock(),
            ) as save_mock,
            patch("core.pipeline.handlers.config.is_step_enabled", new=AsyncMock(return_value=False)),
        ):
            result = await handlers.handle_model_built(fake_db, {"id": "evt-1", "project_id": "proj-1"})

        save_mock.assert_awaited_once()
        assert result == {"skipped": "disabled", "qto_refreshed": True}

    @pytest.mark.asyncio
    async def test_no_suggestion_when_rebar_increased(self, fake_db):
        scene = {"floors": []}
        fake_db.fetch_one.return_value = _row(scene=json.dumps(scene))

        with (
            patch(
                "core.pipeline.handlers.model_qto_summary.fetch_quantity_summary",
                new=AsyncMock(return_value={"payload": {"rebar": {"total_kg": 3000.0}}}),
            ),
            patch(
                "core.pipeline.handlers.model_qto_summary.build_scene_quantities",
                return_value={"project": {"rebar": {"total_kg": 4000.0}}},
            ),
            patch(
                "core.pipeline.handlers.model_qto_summary.save_quantity_summary",
                new=AsyncMock(),
            ),
            patch("core.pipeline.handlers.config.is_step_enabled", new=AsyncMock(return_value=True)),
        ):
            result = await handlers.handle_model_built(fake_db, {"id": "evt-1", "project_id": "proj-1"})

        assert result["suggested"] is False
        assert result["estimated_saving_yuan"] is None


class TestDispatchAndEmitWrapper:
    @pytest.mark.asyncio
    async def test_dispatch_routes_ai_review_completed(self, fake_db):
        mock_handler = AsyncMock(return_value={"suggested": False})

        # dispatch() 按 event_type 从内部 _HANDLERS 表路由，直接替换该表项校验路由行为
        # （替换模块级函数属性不会影响已在模块加载时绑定的表项，见下方对照）。
        with patch.dict("core.pipeline.handlers._HANDLERS", {events.EVENT_AI_REVIEW_COMPLETED: mock_handler}):
            result = await handlers.dispatch(fake_db, {"event_type": events.EVENT_AI_REVIEW_COMPLETED, "project_id": "p1"})

        mock_handler.assert_awaited_once()
        assert result == {"suggested": False}

    @pytest.mark.asyncio
    async def test_dispatch_unknown_event_type_is_skipped_not_failed(self, fake_db):
        result = await handlers.dispatch(fake_db, {"event_type": "something.unknown", "project_id": "p1"})

        assert result == {"skipped": "no_handler", "event_type": "something.unknown"}

    @pytest.mark.asyncio
    async def test_emit_model_built_event_wraps_events_emit(self, fake_db):
        with patch("core.pipeline.handlers.events.emit_event", new=AsyncMock(return_value="evt-9")) as emit_mock:
            event_id = await handlers.emit_model_built_event(fake_db, project_id="proj-1", version=3)

        assert event_id == "evt-9"
        emit_mock.assert_awaited_once()
        _, kwargs = emit_mock.call_args
        assert kwargs["event_type"] == events.EVENT_MODEL_BUILT
        assert kwargs["project_id"] == "proj-1"
        assert kwargs["payload"] == {"version": 3}


# ================================================================
# tasks/pipeline.py
# ================================================================

class FakeDatabase:
    def __init__(self, fetch_one_result=None, fetch_one_side_effect=None):
        if fetch_one_side_effect is not None:
            self.fetch_one = AsyncMock(side_effect=fetch_one_side_effect)
        else:
            self.fetch_one = AsyncMock(return_value=fetch_one_result)
        self.execute = AsyncMock()
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()


class TestPipelineTask:
    @pytest.mark.asyncio
    async def test_process_dispatches_and_marks_done(self):
        from tasks import pipeline as pipeline_task

        fake_db = FakeDatabase(
            fetch_one_result=_row(
                id="evt-1",
                event_type=events.EVENT_AI_REVIEW_COMPLETED,
                project_id="proj-1",
                source_id="drawing-1",
                payload=json.dumps({"total_issues": 2}),
            )
        )

        with (
            patch("tasks.pipeline.databases.Database", return_value=fake_db),
            patch("tasks.pipeline.handlers.dispatch", new=AsyncMock(return_value={"suggested": True})) as dispatch_mock,
        ):
            result = await pipeline_task._process("evt-1")

        assert result == {"event_id": "evt-1", "event_type": events.EVENT_AI_REVIEW_COMPLETED, "suggested": True}
        dispatch_mock.assert_awaited_once()
        # UPDATE status='processing' + mark_event_status(done) 至少两次写
        assert fake_db.execute.await_count >= 2
        fake_db.connect.assert_awaited_once()
        fake_db.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_raises_when_event_missing(self):
        from tasks import pipeline as pipeline_task

        fake_db = FakeDatabase(fetch_one_result=None)

        with patch("tasks.pipeline.databases.Database", return_value=fake_db):
            with pytest.raises(ValueError, match="pipeline_events 记录不存在"):
                await pipeline_task._process("missing-evt")

        fake_db.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_failed_updates_status_and_error(self):
        from tasks import pipeline as pipeline_task

        fake_db = FakeDatabase()

        with patch("tasks.pipeline.databases.Database", return_value=fake_db):
            await pipeline_task._mark_failed("evt-1", "boom")

        fake_db.execute.assert_awaited_once()
        args, kwargs = fake_db.execute.call_args
        params = args[1] if len(args) > 1 else kwargs
        assert params["event_id"] == "evt-1"
        assert params["error"] == "boom"


# ================================================================
# routers/pipeline.py
# ================================================================

@pytest_asyncio.fixture
async def pipeline_client(fake_db, admin_user):
    """独立挂载 routers/pipeline.router 的测试 app（main.py 未注册本路由，见文件边界说明）。"""
    from fastapi import FastAPI

    from dependencies import get_current_user, get_db
    from routers import pipeline as pipeline_router

    test_app = FastAPI()
    test_app.include_router(pipeline_router.router, prefix="/api/v1")
    test_app.dependency_overrides[get_db] = lambda: fake_db
    test_app.dependency_overrides[get_current_user] = lambda: admin_user

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        yield ac


class TestPipelineRouter:
    @pytest.mark.asyncio
    async def test_list_suggestions_defaults_to_open(self, pipeline_client, fake_db):
        fake_db.fetch_all.return_value = [
            _row(
                id="sugg-1", project_id="proj-1", event_id="evt-1",
                suggestion_type="rebuild_model", status="open",
                title="建议重新构建工程模型", summary="...",
                payload={"changed_drawing_count": 2},
                created_at="2026-07-14T00:00:00Z", resolved_at=None, resolved_by=None,
            )
        ]

        resp = await pipeline_client.get("/api/v1/projects/proj-1/pipeline/suggestions")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["suggestion_type"] == "rebuild_model"
        assert body["meta"]["status_filter"] == "open"

    @pytest.mark.asyncio
    async def test_list_suggestions_rejects_invalid_status(self, pipeline_client):
        resp = await pipeline_client.get(
            "/api/v1/projects/proj-1/pipeline/suggestions", params={"status": "bogus"}
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_accept_suggestion_marks_accepted(self, pipeline_client, fake_db):
        fake_db.fetch_one.return_value = _row(
            id="sugg-1", project_id="proj-1", suggestion_type="rebuild_model",
            status="open", title="t", summary="s", payload={},
        )

        resp = await pipeline_client.post(
            "/api/v1/projects/proj-1/pipeline/suggestions/sugg-1/accept"
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "accepted"
        fake_db.execute.assert_awaited()  # UPDATE + write_audit

    @pytest.mark.asyncio
    async def test_accept_suggestion_not_found_returns_404(self, pipeline_client, fake_db):
        fake_db.fetch_one.return_value = None

        resp = await pipeline_client.post(
            "/api/v1/projects/proj-1/pipeline/suggestions/missing/accept"
        )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_accept_suggestion_already_resolved_returns_409(self, pipeline_client, fake_db):
        fake_db.fetch_one.return_value = _row(
            id="sugg-1", project_id="proj-1", suggestion_type="rebuild_model",
            status="accepted", title="t", summary="s", payload={},
        )

        resp = await pipeline_client.post(
            "/api/v1/projects/proj-1/pipeline/suggestions/sugg-1/accept"
        )

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_dismiss_suggestion_marks_dismissed(self, pipeline_client, fake_db):
        fake_db.fetch_one.return_value = _row(
            id="sugg-1", project_id="proj-1", suggestion_type="create_proposal",
            status="open", title="t", summary="s", payload={},
        )

        resp = await pipeline_client.post(
            "/api/v1/projects/proj-1/pipeline/suggestions/sugg-1/dismiss"
        )

        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "dismissed"
