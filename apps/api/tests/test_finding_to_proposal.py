"""Finding → 创效提案草稿测试（Phase D · 泳道2 · D-07）。

覆盖：
- services/finding_service.py 创效潜力判别（规则优先 + LLM 可选增强，含降级路径）
- build_finding_proposal_description 纯函数
- routers/findings.py 新增端点 POST .../findings/{source}/{source_key}/to-proposal
  （envelope、404、409、审计写入、use_llm 开关是否真正传入 router）
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services import finding_service

PROJECT_ID = "11111111-1111-1111-1111-111111111111"


def _finding(**overrides) -> dict:
    base = {
        "id": "engine:e1",
        "source": "engine",
        "source_key": "e1",
        "project_id": PROJECT_ID,
        "drawing_id": "d1",
        "severity": "high",
        "title": "钢筋用量超配",
        "description": "配筋率明显高于计算值，存在材料浪费",
        "status": "pending",
        "location": None,
        "note": None,
        "status_updated_at": None,
        "created_at": None,
    }
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════════
# 规则判别（纯函数）
# ══════════════════════════════════════════════════════════════

def test_rule_based_saving_potential_true_for_high_severity_with_keyword():
    finding = _finding(severity="high", title="钢筋用量超配", description="")
    assert finding_service._rule_based_saving_potential(finding) is True


def test_rule_based_saving_potential_false_for_low_severity():
    finding = _finding(severity="low", title="钢筋用量超配")
    assert finding_service._rule_based_saving_potential(finding) is False


def test_rule_based_saving_potential_false_without_keyword():
    finding = _finding(severity="critical", title="标注缺失", description="未标注比例尺")
    assert finding_service._rule_based_saving_potential(finding) is False


def test_rule_based_saving_potential_true_for_cross_conflict_keyword():
    finding = _finding(severity="high", title="版本冲突：S-01", description="")
    assert finding_service._rule_based_saving_potential(finding) is True


def test_finalize_embeds_has_saving_potential():
    item = {
        "source": "engine", "source_key": "e1", "project_id": PROJECT_ID,
        "drawing_id": None, "severity": "high", "title": "钢筋浪费严重",
        "description": "", "location": None, "created_at": None, "native_status": "open",
    }
    result = finding_service._finalize(item, overlay={})
    assert result["has_saving_potential"] is True


# ══════════════════════════════════════════════════════════════
# assess_saving_potential（规则优先 + LLM 可选增强）
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_assess_saving_potential_rule_hit_skips_llm():
    finding = _finding(severity="high", title="钢筋用量超配")
    mock_router = AsyncMock()

    result = await finding_service.assess_saving_potential(finding, router=mock_router)

    assert result == {"has_saving_potential": True, "source": "rule", "confidence": None, "rationale": None}
    mock_router.route.assert_not_called()


@pytest.mark.asyncio
async def test_assess_saving_potential_no_router_returns_rule_result():
    finding = _finding(severity="critical", title="标注缺失", description="未标注比例尺")

    result = await finding_service.assess_saving_potential(finding, router=None)

    assert result["has_saving_potential"] is False
    assert result["source"] == "rule"


@pytest.mark.asyncio
async def test_assess_saving_potential_low_severity_skips_llm_call():
    finding = _finding(severity="medium", title="标注缺失", description="未标注比例尺")
    mock_router = AsyncMock()

    result = await finding_service.assess_saving_potential(finding, router=mock_router)

    assert result["has_saving_potential"] is False
    mock_router.route.assert_not_called()


@pytest.mark.asyncio
async def test_assess_saving_potential_llm_enhances_when_rule_misses():
    finding = _finding(severity="critical", title="标注缺失", description="未标注比例尺")
    mock_router = AsyncMock()
    mock_router.route.return_value = SimpleNamespace(
        content='{"has_saving_potential": true, "confidence": 0.82, "rationale": "可优化排布"}'
    )

    result = await finding_service.assess_saving_potential(finding, router=mock_router)

    mock_router.route.assert_awaited_once()
    assert mock_router.route.call_args.args[0] == "optimization_hint_writer"
    assert result["has_saving_potential"] is True
    assert result["source"] == "rule+llm"
    assert result["confidence"] == 0.82
    assert result["rationale"] == "可优化排布"


@pytest.mark.asyncio
async def test_assess_saving_potential_llm_negative_stays_rule_source():
    finding = _finding(severity="critical", title="标注缺失", description="未标注比例尺")
    mock_router = AsyncMock()
    mock_router.route.return_value = SimpleNamespace(
        content='{"has_saving_potential": false, "confidence": 0.9, "rationale": "无创效点"}'
    )

    result = await finding_service.assess_saving_potential(finding, router=mock_router)

    assert result["has_saving_potential"] is False
    assert result["source"] == "rule"
    assert result["rationale"] is None  # 否定结果不携带 rationale


@pytest.mark.asyncio
async def test_assess_saving_potential_llm_failure_degrades_gracefully():
    finding = _finding(severity="critical", title="标注缺失", description="未标注比例尺")
    mock_router = AsyncMock()
    mock_router.route.side_effect = RuntimeError("引擎未配置")

    result = await finding_service.assess_saving_potential(finding, router=mock_router)

    assert result == {"has_saving_potential": False, "source": "rule", "confidence": None, "rationale": None}


@pytest.mark.asyncio
async def test_assess_saving_potential_llm_malformed_json_degrades_gracefully():
    finding = _finding(severity="critical", title="标注缺失", description="未标注比例尺")
    mock_router = AsyncMock()
    mock_router.route.return_value = SimpleNamespace(content="not json at all")

    result = await finding_service.assess_saving_potential(finding, router=mock_router)

    assert result["has_saving_potential"] is False
    assert result["source"] == "rule"


# ══════════════════════════════════════════════════════════════
# build_finding_proposal_description（纯函数）
# ══════════════════════════════════════════════════════════════

def test_build_finding_proposal_description_includes_source_and_title():
    finding = _finding()
    desc = finding_service.build_finding_proposal_description(finding)
    assert "engine:e1" in desc
    assert "钢筋用量超配" in desc
    assert "经济师测算与签字" in desc


def test_build_finding_proposal_description_appends_extra_note():
    finding = _finding()
    desc = finding_service.build_finding_proposal_description(finding, "补充：现场已核实")
    assert "补充：现场已核实" in desc


# ══════════════════════════════════════════════════════════════
# Router：POST /projects/{id}/findings/{source}/{source_key}/to-proposal
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_finding_to_proposal_creates_draft_when_rule_matches(client, fake_db):
    finding = _finding()
    fake_db.fetch_one.return_value = {"id": "22222222-2222-2222-2222-222222222222"}

    with patch("services.finding_service.get_finding", return_value=finding):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/findings/engine/e1/to-proposal", json={}
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["status"] == "draft"
    assert body["data"]["proposal_id"] == "22222222-2222-2222-2222-222222222222"
    assert body["data"]["finding_id"] == "engine:e1"
    assert body["data"]["saving_assessment"]["source"] == "rule"

    # 校验实际写库的 proposal_type 默认 A、drawing_id 取自 finding、title 取自 finding
    insert_call = fake_db.fetch_one.call_args
    assert insert_call.args[1] == PROJECT_ID  # project_id
    assert insert_call.args[2] == "d1"        # drawing_id
    assert insert_call.args[4] == "A"         # proposal_type
    assert insert_call.args[5] == "钢筋用量超配"  # title


@pytest.mark.asyncio
async def test_finding_to_proposal_404_when_finding_missing(client, fake_db):
    with patch("services.finding_service.get_finding", return_value=None):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/findings/engine/missing/to-proposal", json={}
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "FINDING_NOT_FOUND"


@pytest.mark.asyncio
async def test_finding_to_proposal_409_when_no_saving_potential(client, fake_db):
    finding = _finding(severity="low", title="标注缺失", description="未标注比例尺")
    with patch("services.finding_service.get_finding", return_value=finding):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/findings/engine/e1/to-proposal", json={}
        )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "NO_SAVING_POTENTIAL"
    fake_db.fetch_one.assert_not_called()  # 拒绝时不应写库


@pytest.mark.asyncio
async def test_finding_to_proposal_rejects_invalid_source(client, fake_db):
    resp = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/findings/bogus/e1/to-proposal", json={}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_SOURCE"


@pytest.mark.asyncio
async def test_finding_to_proposal_default_use_llm_false_passes_no_router(client, fake_db):
    finding = _finding(severity="low", title="标注缺失")  # 规则必不命中，逼迫走 assess 内部 router 分支判断
    fake_db.fetch_one.return_value = {"id": "22222222-2222-2222-2222-222222222222"}

    with patch("services.finding_service.get_finding", return_value=finding), \
         patch("services.finding_service.assess_saving_potential", new_callable=AsyncMock) as mock_assess:
        mock_assess.return_value = {
            "has_saving_potential": True, "source": "rule", "confidence": None, "rationale": None,
        }
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/findings/engine/e1/to-proposal", json={"use_llm": False}
        )

    assert resp.status_code == 201
    assert mock_assess.call_args.kwargs["router"] is None


@pytest.mark.asyncio
async def test_finding_to_proposal_use_llm_true_passes_router(client, fake_db):
    finding = _finding(severity="critical", title="标注缺失")
    fake_db.fetch_one.return_value = {"id": "22222222-2222-2222-2222-222222222222"}

    with patch("services.finding_service.get_finding", return_value=finding), \
         patch("services.finding_service.assess_saving_potential", new_callable=AsyncMock) as mock_assess:
        mock_assess.return_value = {
            "has_saving_potential": True, "source": "rule+llm", "confidence": 0.7, "rationale": "r",
        }
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/findings/engine/e1/to-proposal", json={"use_llm": True}
        )

    assert resp.status_code == 201
    assert mock_assess.call_args.kwargs["router"] is not None


@pytest.mark.asyncio
async def test_finding_to_proposal_custom_title_and_type_override(client, fake_db):
    finding = _finding()
    fake_db.fetch_one.return_value = {"id": "22222222-2222-2222-2222-222222222222"}

    with patch("services.finding_service.get_finding", return_value=finding):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/findings/engine/e1/to-proposal",
            json={"title": "自定义标题", "proposal_type": "B", "raw_saving_est": 1000.0},
        )

    assert resp.status_code == 201
    insert_call = fake_db.fetch_one.call_args
    assert insert_call.args[4] == "B"
    assert insert_call.args[5] == "自定义标题"
    assert insert_call.args[7] == 1000.0
