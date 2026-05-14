from __future__ import annotations

import json
import time
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import fitz
import openpyxl
import pytest

from core import auth
from core.ai_review import langgraph_agent
from core.llm.circuit_breaker import CBState, CircuitBreaker
from services import ai_report_generator, bonus_calculator, certificate_generator


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.data[key] = value


class FakeRouter:
    def __init__(self, responses: list[str] | None = None, fail: bool = False):
        self.responses = responses or []
        self.fail = fail
        self.calls: list[tuple[str, list[dict]]] = []

    async def route(self, engine: str, messages: list[dict]):
        self.calls.append((engine, messages))
        if self.fail:
            raise RuntimeError("llm down")
        content = self.responses.pop(0) if self.responses else '{"issues":[],"summary":"ok"}'
        return SimpleNamespace(content=content)


def test_auth_hash_verify_and_tokens_roundtrip():
    hashed = auth.hash_password("secret")
    assert auth.verify_password("secret", hashed)
    assert not auth.verify_password("wrong", hashed)

    access = auth.create_access_token({"sub": "u1", "role": "group_admin"})
    refresh = auth.create_refresh_token({"sub": "u1", "role": "group_admin"})
    assert auth.decode_token(access)["sub"] == "u1"
    assert auth.decode_token(refresh)["type"] == "refresh"


def test_bonus_calculator_distribution_and_validation():
    snapshot = bonus_calculator.calculate(Decimal("100000"), Decimal("0.15"), "u1")
    assert snapshot["bonus_pool"] == 15000.0
    assert snapshot["group_amount"] + snapshot["team_pool"] + snapshot["proposer_amount"] == 15000.0
    assert bonus_calculator.amounts_from_snapshot(snapshot) == (
        Decimal("3000.0"),
        Decimal("7500.0"),
        Decimal("4500.0"),
    )

    with pytest.raises(ValueError):
        bonus_calculator.calculate(Decimal("0"))
    with pytest.raises(ValueError):
        bonus_calculator.calculate(Decimal("100"), Decimal("0.99"))


@pytest.mark.asyncio
async def test_circuit_breaker_state_transitions(monkeypatch):
    redis = FakeRedis()
    cb = CircuitBreaker(redis, "unit", failure_threshold=2, success_threshold=2, recovery_sec=10)

    assert await cb.state() == CBState.CLOSED
    await cb.record_failure()
    assert await cb.state() == CBState.CLOSED
    await cb.record_failure()
    assert await cb.is_open()

    raw = json.loads(redis.data["cb:unit"])
    raw["opened_at"] = time.time() - 11
    redis.data["cb:unit"] = json.dumps(raw)
    assert await cb.state() == CBState.HALF_OPEN
    await cb.record_success()
    assert await cb.state() == CBState.HALF_OPEN
    await cb.record_success()
    assert await cb.state() == CBState.CLOSED


@pytest.mark.asyncio
async def test_langgraph_fallback_and_json_parsing(monkeypatch):
    monkeypatch.setattr(langgraph_agent, "_run_with_graph", MagicMock(side_effect=RuntimeError("boom")))
    router = FakeRouter([
        '```json\n{"issues":[{"severity":"info","description":"ok"}],"summary":"完成"}\n```'
    ])

    issues, summary = await langgraph_agent.run_langgraph_agent("图纸", "规范", router)
    assert issues[0]["severity"] == "info"
    assert summary == "完成"
    assert langgraph_agent._parse_json("not-json", {"fallback": True}) == {"fallback": True}
    assert langgraph_agent._make_state("a", "b")["drawing_info"] == "a"


@pytest.mark.asyncio
async def test_langgraph_nodes_handle_bad_llm_payloads():
    state = langgraph_agent._make_state("建筑图纸", "规范摘要")
    router = FakeRouter(["{}", "not-json", '{"issues":[{"severity":"minor"}],"summary":"ok"}'])

    identified = await langgraph_agent._identify_node(state, router)
    assert identified["problem_points"] == []
    looked_up = await langgraph_agent._lookup_node({**identified, "problem_points": ["疏散"]}, router)
    assert looked_up["regulation_refs"] == []
    synthesized = await langgraph_agent._synthesize_node(looked_up, router)
    assert synthesized["summary"] == "ok"


def _sample_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 20), "sample")
    data = doc.tobytes()
    doc.close()
    return data


def test_ai_report_generators_create_pdf_and_excel():
    issues = [
        {
            "engine": "rule",
            "severity": "critical",
            "category": "强条",
            "description": "坐标问题",
            "regulation_ref": "GB 1.0",
            "suggestion": "修改",
            "status": "open",
            "location_x": 0.5,
            "location_y": 0.5,
        },
        {
            "engine": "kg",
            "severity": "info",
            "category": "表达",
            "description": "无坐标问题",
            "regulation_ref": "",
            "suggestion": "补充",
            "status": "acknowledged",
        },
    ]
    pdf = ai_report_generator.generate_annotated_pdf(_sample_pdf(), issues)
    assert pdf.startswith(b"%PDF")

    xlsx = ai_report_generator.generate_excel_report(
        issues, "E2E-001", "architecture", report_date=datetime(2026, 1, 2)
    )
    workbook = openpyxl.load_workbook(filename=__import__("io").BytesIO(xlsx))
    assert "汇总" in workbook.sheetnames
    assert workbook["汇总"]["B2"].value == "E2E-001"


def test_certificate_generator_outputs_pdf():
    pdf = certificate_generator.generate_certificate(
        proposal={
            "id": "proposal-1",
            "title": "优化提案",
            "proposal_type": "A",
            "net_saving": 100000,
        },
        distribution={"group_amount": 3000, "team_pool": 7500, "proposer_amount": 4500},
        approvals=[
            {"role": "project_manager", "signed_at": "2026-01-01T00:00:00Z"},
            {"role": "economist", "signed_at": datetime(2026, 1, 2)},
        ],
        proposer_name="张三",
        project_name="示范项目",
    )
    assert pdf.startswith(b"%PDF")
