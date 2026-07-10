"""C-12 符号 spotting 推理服务 + Router 集成测试（离线 mock，无 GPU 可跑）。

覆盖：
- SpottingService 用 mock 后端从合成图元产出候选；
- 后端选择逻辑（首个可用优先 / 不可用回退 mock / 探测异常视为不可用）；
- 优雅降级（后端 spot 抛异常、预处理失败均不跨边界抛）；
- 异步调用日志写 symbol_spotting_logs（含无事件循环时静默跳过）；
- Router 端点返回统一信封 success/data/error/meta（含 404 / 409）。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from core.model3d.preprocess.schema import Primitive, PrimitiveDoc
from core.model3d.spotting import mock_backend as mock_backend_mod
from core.model3d.spotting.mock_backend import MockSpottingBackend
from core.model3d.spotting.service import (
    ENGINE_NAME,
    SpottingService,
    _default_backends,
    _load_cadtransformer_backend,
)
from core.model3d.spotting.types import SpottingResult, SymbolCandidate

PROJECT_ID = "22222222-2222-2222-2222-222222222222"
DRAWING_ID = "55555555-5555-5555-5555-555555555555"


# ── 测试替身 ──────────────────────────────────────────────────

class _Backend:
    """可配置可用性 / spot 行为的假后端。"""

    def __init__(self, name: str, available: bool = True, result: SpottingResult | None = None,
                 raises: bool = False, avail_raises: bool = False):
        self.name = name
        self._available = available
        self._result = result if result is not None else SpottingResult(backend=name)
        self._raises = raises
        self._avail_raises = avail_raises

    def is_available(self) -> bool:
        if self._avail_raises:
            raise RuntimeError("probe boom")
        return self._available

    def spot(self, doc: PrimitiveDoc) -> SpottingResult:
        if self._raises:
            raise RuntimeError("spot boom")
        return self._result


def _synthetic_doc() -> PrimitiveDoc:
    """含可识别图层的合成图元文档（mock 后端据图层弱标签产候选）。"""
    prims = (
        Primitive(id=0, type="line", points=((0.0, 0.0), (0.0, 300.0)), layer="S-COLU"),
        Primitive(id=1, type="line", points=((0.0, 0.0), (500.0, 0.0)), layer="A-WALL"),
        Primitive(id=2, type="line", points=((0.0, 0.0), (400.0, 0.0)), layer="S-BEAM"),
    )
    return PrimitiveDoc(page_w=500.0, page_h=800.0, primitives=prims)


# ── 服务：mock 后端产候选 ─────────────────────────────────────

def test_spot_doc_produces_candidates_from_synthetic_primitives():
    # Arrange
    service = SpottingService(backends=[MockSpottingBackend()])

    # Act
    result = service.spot_doc(_synthetic_doc())

    # Assert
    assert result.backend == "mock"
    assert len(result.candidates) == 3
    assert set(result.counts) == {"column", "wall", "beam"}
    assert all(0.0 <= c.confidence <= 1.0 for c in result.candidates)


def test_spot_drawing_unknown_ext_degrades_without_raising():
    # 预处理不支持的扩展名 → 空文档 + warning；mock 产 0 候选；不抛异常。
    service = SpottingService(backends=[MockSpottingBackend()])

    result = service.spot_drawing(b"not-a-cad-file", "xyz")

    assert result.candidates == ()
    assert any("不支持" in w or "降级" in w for w in result.warnings)


# ── 服务：后端选择逻辑 ────────────────────────────────────────

def test_select_backend_prefers_first_available():
    primary = _Backend("primary", available=True)
    service = SpottingService(backends=[primary, MockSpottingBackend()])

    assert service.select_backend() is primary


def test_select_backend_falls_back_to_mock_when_primary_unavailable():
    down = _Backend("primary", available=False)
    mock = MockSpottingBackend()
    service = SpottingService(backends=[down, mock])

    assert service.select_backend() is mock


def test_select_backend_returns_mock_when_all_unavailable():
    down = _Backend("primary", available=False)
    service = SpottingService(backends=[down])

    chosen = service.select_backend()

    assert chosen.name == "mock"  # 兜底新建 MockSpottingBackend


def test_availability_probe_exception_treated_as_unavailable():
    boom = _Backend("boom", avail_raises=True)
    mock = MockSpottingBackend()
    service = SpottingService(backends=[boom, mock])

    assert service.select_backend() is mock
    listed = {b["name"]: b["available"] for b in service.list_backends()}
    assert listed["boom"] is False
    assert listed["mock"] is True


def test_default_backends_end_with_mock_fallback():
    # 默认链末位恒为 mock 兜底；CADTransformer(C-08) 懒加载可能为 None（未落地）
    # 或返回实例（已落地，但无 GPU/权重时 is_available()=False，仍回退 mock）。
    backends = _default_backends()
    assert backends[-1].name == "mock"


def test_cadtransformer_unavailable_in_ci_selects_mock():
    # C-08 已落地：默认链首位 CADTransformer 在无 GPU/权重的 CI 下不可用 → 选路回退 mock。
    service = SpottingService()  # 默认链
    assert service.select_backend().name == "mock"
    cad = _load_cadtransformer_backend()
    if cad is not None:               # C-08 已落地
        assert cad.is_available() is False


# ── 服务：优雅降级 ────────────────────────────────────────────

def test_spot_doc_degrades_when_backend_raises():
    broken = _Backend("broken", available=True, raises=True)
    service = SpottingService(backends=[broken])

    result = service.spot_doc(_synthetic_doc())

    assert result.backend == "broken"
    assert result.candidates == ()
    assert any("降级" in w for w in result.warnings)


# ── 服务：调用日志（ModelRouter 治理 · 日志基础设施）────────────

@pytest.mark.asyncio
async def test_log_inserts_into_symbol_spotting_logs(fake_db):
    service = SpottingService(db=fake_db)

    await service._log(
        backend="mock", candidate_count=3, latency_ms=12,
        success=True, error=None, project_id=PROJECT_ID, drawing_id=DRAWING_ID,
    )

    fake_db.execute.assert_awaited_once()
    kwargs = fake_db.execute.await_args.kwargs
    assert kwargs["engine_name"] == ENGINE_NAME
    assert kwargs["backend"] == "mock"
    assert kwargs["candidate_count"] == 3
    assert kwargs["success"] is True


@pytest.mark.asyncio
async def test_log_swallows_db_errors(fake_db):
    fake_db.execute.side_effect = RuntimeError("db down")
    service = SpottingService(db=fake_db)

    # 不抛异常即通过（日志失败不影响主流程）
    await service._log(
        backend="mock", candidate_count=0, latency_ms=1,
        success=False, error="boom", project_id=None, drawing_id=None,
    )


@pytest.mark.asyncio
async def test_spot_doc_schedules_async_log_when_loop_running(fake_db):
    service = SpottingService(db=fake_db, backends=[MockSpottingBackend()])

    service.spot_doc(_synthetic_doc(), project_id=PROJECT_ID, drawing_id=DRAWING_ID)
    await asyncio.sleep(0)  # 让 fire-and-forget 日志任务落地

    fake_db.execute.assert_awaited()
    assert fake_db.execute.await_args.kwargs["engine_name"] == ENGINE_NAME


def test_record_log_skips_without_running_loop(fake_db):
    # 同步上下文（无事件循环）→ 静默跳过，绝不抛异常。
    service = SpottingService(db=fake_db)
    service._record_log(
        backend="mock", candidate_count=1, latency_ms=1,
        success=True, error=None, project_id=None, drawing_id=None,
    )
    fake_db.execute.assert_not_awaited()


def test_record_log_noop_without_db():
    service = SpottingService(db=None, backends=[MockSpottingBackend()])
    # 无 db → spot_doc 正常返回且不尝试日志
    result = service.spot_doc(_synthetic_doc())
    assert len(result.candidates) == 3


# ── Router：统一信封 ──────────────────────────────────────────

def _fake_service(result: SpottingResult) -> SpottingService:
    svc = SpottingService(backends=[MockSpottingBackend()])
    svc.spot_drawing = lambda *a, **k: result          # type: ignore[method-assign]
    return svc


@pytest.mark.asyncio
async def test_spot_endpoint_returns_unified_envelope(client, fake_db):
    from routers.model_spotting import get_spotting_service
    from main import app

    fake_db.fetch_one.return_value = {"id": DRAWING_ID, "file_key": "projects/p/plan.dxf"}
    result = SpottingResult(
        candidates=(SymbolCandidate(category="column", confidence=0.9, bbox=(0, 0, 1, 1)),),
        backend="mock",
    )
    app.dependency_overrides[get_spotting_service] = lambda: _fake_service(result)

    with patch("routers.model_spotting.get_file_bytes", return_value=b"bytes"):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/drawings/{DRAWING_ID}/spot"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["backend"] == "mock"
    assert body["data"]["candidates"][0]["category"] == "column"
    assert body["meta"]["engine_name"] == ENGINE_NAME
    assert body["meta"]["backend"] == "mock"
    assert body["meta"]["candidate_count"] == 1


@pytest.mark.asyncio
async def test_spot_endpoint_full_stack_with_mock_backend(client, fake_db):
    # 真实穿过 SpottingService + 预处理 + mock 后端（垃圾字节 → 预处理降级 → 0 候选）。
    fake_db.fetch_one.return_value = {"id": DRAWING_ID, "file_key": "projects/p/plan.dxf"}

    with patch("routers.model_spotting.get_file_bytes", return_value=b"garbage"):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/drawings/{DRAWING_ID}/spot"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["backend"] == "mock"
    assert body["meta"]["candidate_count"] == 0


@pytest.mark.asyncio
async def test_spot_endpoint_404_when_drawing_missing(client, fake_db):
    fake_db.fetch_one.return_value = None

    resp = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/drawings/{DRAWING_ID}/spot"
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "DRAWING_NOT_FOUND"


@pytest.mark.asyncio
async def test_spot_endpoint_409_when_file_key_missing(client, fake_db):
    fake_db.fetch_one.return_value = {"id": DRAWING_ID, "file_key": None}

    resp = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/drawings/{DRAWING_ID}/spot"
    )

    assert resp.status_code == 409
    assert resp.json()["detail"] == "DRAWING_FILE_MISSING"


@pytest.mark.asyncio
async def test_spot_endpoint_502_when_download_fails(client, fake_db):
    fake_db.fetch_one.return_value = {"id": DRAWING_ID, "file_key": "projects/p/plan.dxf"}

    with patch("routers.model_spotting.get_file_bytes", side_effect=RuntimeError("minio down")):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/drawings/{DRAWING_ID}/spot"
        )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "DRAWING_DOWNLOAD_FAILED"


@pytest.mark.asyncio
async def test_spot_backends_endpoint_reports_active(client, fake_db):
    fake_db.fetch_one.return_value = {"id": DRAWING_ID, "file_key": "projects/p/plan.dxf"}

    resp = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/drawings/{DRAWING_ID}/spot/backends"
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    names = {b["name"] for b in body["data"]["backends"]}
    assert "mock" in names
    assert body["data"]["active"] == "mock"
