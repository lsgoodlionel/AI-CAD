"""Phase D 里程碑 E2E Demo（D-23）：合成整套图 → 上传 → AI 审图 → Finding 聚合
→ 事件编排自动生成建议 → 算量 → Finding 转创效提案草稿，离线端到端串联全链路。

沿用 Phase B/C Demo 风格（tests/e2e/test_phase_b_demo.py / test_phase_c_demo.py）：
DB/Celery 全部 mock（不需要真实 PostgreSQL/Redis），直接驱动生产模块的公开函数/
路由，断言链路真正串联起来，而不是重新实现一遍业务逻辑。

链路：
    tasks/ai_review.py 审图完成
        → core/pipeline/events.emit_event(ai_review.completed)          [自动]
        → tasks/pipeline.process_pipeline_event → handlers.dispatch     [自动]
        → handlers.handle_ai_review_completed → 「建议重建模型」待办     [自动，超阈值]
    tasks/model_build.py 建模完成
        → core/pipeline/handlers.emit_model_built_event(model.built)    [自动]
        → handlers.handle_model_built → QTO 刷新 + 「建议创建创效提案」  [自动，超阈值]
    services/finding_service.py 聚合五类问题为 Finding
        → routers/findings.py POST .../to-proposal → 创效提案 **draft**  [人工显式调用]
    routers/pipeline.py POST .../accept
        → 仅状态流转 + 审计，**不**代为触发重建/建提案等硬动作            [人工确认]

核心断言（对照 D-23 验收）：
  ①该自动的地方无需手动触发（ai_review.completed / model.built 均在生产任务代码
    内自动发射事件，事件消费自动派生建议，全程无需人工介入）
  ②该人工确认的地方仍是建议制：Finding→提案是显式 POST 调用产出 draft（不触发
    经济师签字/公示/分配），建议 accept 仅是状态标记（不代为执行硬动作）
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.pipeline import events, handlers
from dependencies import get_current_user, get_db
from routers import findings as findings_router
from routers import pipeline as pipeline_router
from services import finding_service

_API_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _API_ROOT.parent.parent
_DOCS = _REPO_ROOT / "docs"

PROJECT_ID = "22222222-2222-2222-2222-222222222222"


def _row(**values):
    return dict(values)


class FakeDatabase:
    """轻量 Mock，覆盖 execute / fetch_one / fetch_all（对齐 tests/test_pipeline.py 同名类）。"""

    def __init__(self, *, fetch_one_side_effect=None, fetch_all_return=None):
        self.fetch_one = AsyncMock(side_effect=fetch_one_side_effect) if fetch_one_side_effect else AsyncMock(return_value=None)
        self.fetch_all = AsyncMock(return_value=fetch_all_return or [])
        self.execute = AsyncMock()
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()


# ── 合成整套图触发的下游数据（对齐 Phase B Demo 的合成整套图风格）────

def _synthetic_scene() -> dict:
    """一个已建模项目的最小 scene——足够 build_scene_quantities 算出钢筋量。"""
    return {
        "floors": [{
            "key": "F1", "label": "1层", "building_units": ["main"],
            "elements": {
                "columns": [{"outline": [[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]]}],
                "beams": [{"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6}],
                "slabs": [{"outline": [[0, 0], [6, 0], [6, 6], [0, 6]], "thickness": 0.12}],
                "walls": [],
            },
        }],
        "quality": {"story_tables": {"main": [{"story_key": "F1", "height_m": 3.0}]}},
    }


# ════════════════════════════════════════════════════════════════
# 断言①：ai_review.completed 自动触发「建议重建模型」——无需人工介入
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_step1_ai_review_completion_auto_emits_and_creates_rebuild_suggestion():
    """驱动 tasks/ai_review.py 真实调用的两个入口：events.emit_event → tasks/pipeline._process
    → handlers.dispatch，串联验证「审图完成」到「生成建议」全程零人工触发。
    """
    # ── 1a. tasks/ai_review.py 完成审图后调用的发射点（源码已核实见下方 test_source_wiring）
    emit_db = FakeDatabase(fetch_one_side_effect=[_row(id="evt-1")])
    with patch("tasks.pipeline.process_pipeline_event") as task:
        task.delay = MagicMock()
        event_id = await events.emit_event(
            emit_db,
            event_type=events.EVENT_AI_REVIEW_COMPLETED,
            project_id=PROJECT_ID,
            source_id="drawing-1",
            payload={"report_id": "rep-1", "total_issues": 5, "critical_issues": 1},
        )
    assert event_id == "evt-1"
    # 事件落库后 Celery 消费任务被自动派发（不是人工点了什么按钮）
    task.delay.assert_called_once_with("evt-1")

    # ── 1b. tasks/pipeline._process 消费事件 → handlers.dispatch → 生成建议
    # 项目级开关/阈值走 core/pipeline/config.py（已有独立单测覆盖，见 test_pipeline.py::TestConfig）；
    # 本处直接给定确定性开关值，聚焦验证「_process → dispatch → handler → 建议落库」这条链路真正接上，
    # 而不是重新验证 config 的读取细节。
    process_db = FakeDatabase(
        fetch_one_side_effect=[
            _row(  # SELECT pipeline_events
                id="evt-1", event_type=events.EVENT_AI_REVIEW_COMPLETED,
                project_id=PROJECT_ID, source_id="drawing-1",
                payload=json.dumps({"total_issues": 5, "critical_issues": 1}),
            ),
            None,  # handlers._compute_rebuild_impact: project_models 尚未建过模
            _row(id="sugg-rebuild-1"),  # _upsert_suggestion 落库返回
        ],
        fetch_all_return=[
            _row(id="d1", drawing_no="A-101"),
            _row(id="d2", drawing_no="A-501"),
        ],
    )
    with (
        patch("tasks.pipeline.databases.Database", return_value=process_db),
        patch("core.pipeline.handlers.config.is_step_enabled", new=AsyncMock(return_value=True)),
        patch("core.pipeline.handlers.config.get_rebuild_impact_min_drawings", new=AsyncMock(return_value=1)),
    ):
        from tasks import pipeline as pipeline_task
        result = await pipeline_task._process("evt-1")

    assert result["event_type"] == events.EVENT_AI_REVIEW_COMPLETED
    assert result["suggested"] is True
    assert result["suggestion_id"] == "sugg-rebuild-1"
    assert result["changed_drawing_count"] == 2
    assert result["model_ever_built"] is False
    # 事件被标记为 processing → done（可审计、可重放），全程无 mock 之外的人工调用
    assert process_db.execute.await_count >= 2


def test_step1b_ai_review_task_source_wires_emit_on_completion():
    """核实 tasks/ai_review.py 在审图主流程完成后**确实**无条件调用发射点
    （而不仅仅是 handlers 单测里 mock 出来的假设）——源码级证据。"""
    src = (_API_ROOT / "tasks" / "ai_review.py").read_text(encoding="utf-8")
    assert "pipeline_events.emit_event" in src
    assert "EVENT_AI_REVIEW_COMPLETED" in src
    # 紧跟在 drawings.status='ai_done' 落库之后（同一事务性流程内，非旁路脚本触发）
    assert src.index("status='ai_done'") < src.index("EVENT_AI_REVIEW_COMPLETED")


# ════════════════════════════════════════════════════════════════
# 断言②：model.built 自动刷新 QTO + 超阈值生成「建议创建创效提案」
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_step2_model_built_auto_refreshes_qto_and_creates_proposal_suggestion():
    scene = _synthetic_scene()
    db = FakeDatabase(
        fetch_one_side_effect=[
            _row(scene=json.dumps(scene)),   # SELECT project_models.scene
            _row(id="sugg-proposal-1"),      # _upsert_suggestion 落库返回
        ],
    )

    with (
        patch(
            "core.pipeline.handlers.model_qto_summary.fetch_quantity_summary",
            new=AsyncMock(return_value={"payload": {"rebar": {"total_kg": 12000.0}}}),
        ),
        patch(
            "core.pipeline.handlers.model_qto_summary.build_scene_quantities",
            return_value={"project": {"rebar": {"total_kg": 4000.0}}},
        ) as build_mock,
        patch(
            "core.pipeline.handlers.model_qto_summary.save_quantity_summary",
            new=AsyncMock(),
        ) as save_mock,
        patch("core.pipeline.handlers.config.is_step_enabled", new=AsyncMock(return_value=True)),
        patch("core.pipeline.handlers.config.get_steel_price_per_ton", new=AsyncMock(return_value=4500.0)),
        patch("core.pipeline.handlers.config.get_qto_saving_threshold_yuan", new=AsyncMock(return_value=5000.0)),
    ):
        result = await handlers.handle_model_built(db, {"id": "evt-2", "project_id": PROJECT_ID})

    # QTO 用既有的 build_scene_quantities 真实重算并持久化（不是绕开算量层臆造数字）
    build_mock.assert_called_once()
    save_mock.assert_awaited_once()
    assert result["qto_refreshed"] is True
    # 钢筋量 12000kg → 4000kg，按默认钢价 4500 元/吨： (12000-4000)/1000*4500 = 36000 > 5000 门槛
    assert result["estimated_saving_yuan"] == pytest.approx(36000.0)
    assert result["suggested"] is True
    assert result["suggestion_id"] == "sugg-proposal-1"


def test_step2b_model_build_task_source_wires_emit_on_completion():
    """核实 tasks/model_build.py 建模成功落库后**确实**调用 emit_model_built_event
    （handlers.py 文件头注释称「本工作块未接线」已过期——源码级复核纠偏，见返回报告）。"""
    src = (_API_ROOT / "tasks" / "model_build.py").read_text(encoding="utf-8")
    assert "emit_model_built_event" in src
    assert src.index("status='ready'") < src.index("emit_model_built_event")


# ════════════════════════════════════════════════════════════════
# 断言③：Finding 聚合 → 创效潜力规则判别 → 转提案**必须人工显式调用**，
#         产出 draft，不触发经济师签字/公示（硬约束不被自动化绕过）
# ════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def findings_client():
    """独立挂载 findings + pipeline 路由的测试 app（main.py 未注册，对齐 test_pipeline.py 模式）。"""
    fake_db = FakeDatabase()
    user = {"id": "33333333-3333-3333-3333-333333333333", "role": "pm", "username": "demo_pm"}

    test_app = FastAPI()
    test_app.include_router(findings_router.router, prefix="/api/v1")
    test_app.include_router(pipeline_router.router, prefix="/api/v1")
    test_app.dependency_overrides[get_db] = lambda: fake_db
    test_app.dependency_overrides[get_current_user] = lambda: user

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        yield ac, fake_db, user


def _finding(**overrides) -> dict:
    """对齐 tests/test_finding_to_proposal.py 同名 helper 的最小 Finding 形态。"""
    base = {
        "id": "engine:iss-1", "source": "engine", "source_key": "iss-1",
        "project_id": PROJECT_ID, "drawing_id": "ddd-1", "severity": "high",
        "title": "钢筋超配", "description": "梁纵筋配置量超设计规范 20%，存在材料浪费",
        "status": "pending", "location": None, "note": None,
        "status_updated_at": None, "created_at": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_step3_finding_with_saving_potential_converts_to_draft_proposal_only(findings_client):
    ac, fake_db, _user = findings_client
    finding = _finding()
    fake_db.fetch_one.return_value = _row(id="proposal-draft-1")

    # 走真实聚合来源函数会与 overlay 的第二次 fetch_all 混叠（同一 AsyncMock 单一
    # return_value 无法区分两次不同语义的查询），故对齐既有测试模式，在
    # get_finding 这一层打桩——被测的是「转提案」这一步的行为，Finding 聚合本身
    # 已由 tests/test_findings.py 独立覆盖。
    with patch("routers.findings.finding_service.get_finding", return_value=finding):
        resp = await ac.post(
            f"/api/v1/projects/{PROJECT_ID}/findings/engine/iss-1/to-proposal",
            json={"note": "D-23 demo 自动串联验证"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    # 仅产出 draft——三审签字硬约束未被绕过（草稿描述里显式标注需经济师测算/签字）
    assert body["data"]["status"] == "draft"
    assert body["data"]["saving_assessment"]["source"] == "rule"
    assert body["data"]["saving_assessment"]["has_saving_potential"] is True

    inserted_sql = fake_db.fetch_one.call_args.args[0]
    assert "incentive_proposals" in inserted_sql
    inserted_description = fake_db.fetch_one.call_args.args[6]
    assert "需经二审经济师测算与签字" in inserted_description

    # 全程未触碰任何签字/发布/分配相关端点或 SQL——转提案是「造草稿」，不是「代为审批」
    all_sql_calls = [c.args[0] for c in fake_db.fetch_one.call_args_list if c.args]
    assert not any("economist_signed_at" in sql for sql in all_sql_calls)
    assert not any("published" in sql for sql in all_sql_calls)


@pytest.mark.asyncio
async def test_step3b_finding_without_saving_potential_is_rejected_not_silently_drafted(findings_client):
    """规则未命中且未显式 use_llm=True → 409，绝不静默造一条空提案（诚实降级，非自动兜底通过）。"""
    ac, fake_db, _user = findings_client
    finding = _finding(
        source_key="iss-2", severity="low", title="文字标注不清晰",
        description="图纸右下角文字模糊，建议重新出图",
    )

    with patch("routers.findings.finding_service.get_finding", return_value=finding):
        resp = await ac.post(f"/api/v1/projects/{PROJECT_ID}/findings/engine/iss-2/to-proposal", json={})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "NO_SAVING_POTENTIAL"
    fake_db.execute.assert_not_awaited()  # 无提案被造出、无审计写入
    fake_db.fetch_one.assert_not_called()  # 拒绝时不写库


# ════════════════════════════════════════════════════════════════
# 断言④：建议采纳（accept）只是状态标记 + 审计，绝不代为触发硬动作
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_step4_accept_suggestion_only_flips_status_never_triggers_hard_action(findings_client):
    ac, fake_db, _user = findings_client

    fake_db.fetch_one.return_value = _row(
        id="sugg-rebuild-1", project_id=PROJECT_ID, suggestion_type="rebuild_model",
        status="open", title="建议重新构建工程模型", summary="...", payload={},
    )

    resp = await ac.post(
        f"/api/v1/projects/{PROJECT_ID}/pipeline/suggestions/sugg-rebuild-1/accept"
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "accepted"

    # accept 全链路仅两次写：UPDATE pipeline_suggestions + INSERT audit_logs——
    # 没有第三次写触及 project_models / incentive_proposals，证明它不代为执行重建/建提案。
    assert fake_db.execute.await_count == 2
    executed_sql = [c.args[0] for c in fake_db.execute.call_args_list]
    assert any("pipeline_suggestions" in sql for sql in executed_sql)
    assert any("audit_logs" in sql for sql in executed_sql)
    assert not any("project_models" in sql for sql in executed_sql)
    assert not any("incentive_proposals" in sql for sql in executed_sql)


@pytest.mark.asyncio
async def test_step4b_dismiss_is_symmetric_and_equally_inert(findings_client):
    ac, fake_db, _user = findings_client

    fake_db.fetch_one.return_value = _row(
        id="sugg-proposal-1", project_id=PROJECT_ID, suggestion_type="create_proposal",
        status="open", title="建议创建创效提案", summary="...", payload={},
    )

    resp = await ac.post(
        f"/api/v1/projects/{PROJECT_ID}/pipeline/suggestions/sugg-proposal-1/dismiss"
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "dismissed"
    assert fake_db.execute.await_count == 2  # 与 accept 同等份量：状态 + 审计，无额外硬动作


# ════════════════════════════════════════════════════════════════
# 断言⑤：项目级开关关闭时，自动化不生成建议（自动化本身可控，非强制）
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_step5_pipeline_step_disabled_skips_suggestion_generation():
    db = FakeDatabase()
    with patch("core.pipeline.handlers.config.is_step_enabled", new=AsyncMock(return_value=False)):
        result = await handlers.handle_ai_review_completed(db, {"id": "evt-x", "project_id": PROJECT_ID})

    assert result == {"skipped": "disabled"}
    db.fetch_one.assert_not_awaited()  # 关闭时甚至不查询，尊重项目自主开关


# ════════════════════════════════════════════════════════════════
# 断言⑥：全链路契约文件齐备（迁移 + 文档，验收可复核）
# ════════════════════════════════════════════════════════════════

def test_standard_contract_files_present():
    mig = (_API_ROOT / "migrations" / "027_pipeline_events.sql").read_text(encoding="utf-8")
    assert "pipeline_events" in mig and "pipeline_suggestions" in mig
    assert "rebuild_model" in mig and "create_proposal" in mig

    findings_mig = (_API_ROOT / "migrations" / "026_findings.sql").read_text(encoding="utf-8")
    assert "finding_status" in findings_mig

    # Finding 五类来源在服务层齐备（本 Demo 只驱动 engine 一类，其余四类已由
    # tests/test_findings.py 单测覆盖，此处仅核实契约常量未漂移）。
    assert finding_service.VALID_SOURCES == frozenset({"engine", "review", "cross", "semantic", "symbol"})

    assert (_DOCS / "PHASE_D_DEMO.md").exists()
