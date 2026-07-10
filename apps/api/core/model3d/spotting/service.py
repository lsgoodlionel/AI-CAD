"""C-12 符号 spotting 推理服务（接 ModelRouter 引擎治理）。

符号 spotting 是 CV 模型（非 LLM chat），故「接 ModelRouter」取其**引擎治理**语义：
- 引擎注册：迁移 023 为 ``symbol_spotting`` 种子 ``engine_model_configs``（primary/fallback），
  与 018 VLM 引擎同范式，纳入管理后台「引擎配置」统一治理与配置漂移控制。
- 调用日志：异步写 ``symbol_spotting_logs``（仿 ``core/llm/router.py:_log``；CV 指标
  candidate_count/backend/latency 不套 LLM token 列，故用专用表，见迁移 023 注释）。
- 断路器/回退：以「后端可用性探测 + 有序回退到 mock」实现降级，无 GPU/权重也不硬失败。

推理本身不走 LLM message 契约，而是走 ``SpottingBackend.spot(PrimitiveDoc)``。
默认后端顺序：CADTransformerBackend(C-08，懒加载可选) → MockSpottingBackend(离线兜底)。
"""
from __future__ import annotations

import asyncio
import logging
import time

from core.model3d.preprocess import preprocess_drawing
from core.model3d.preprocess.schema import PrimitiveDoc

from .mock_backend import MockSpottingBackend
from .types import SpottingBackend, SpottingResult

logger = logging.getLogger(__name__)

ENGINE_NAME = "symbol_spotting"

_LOG_INSERT_SQL = """
INSERT INTO symbol_spotting_logs
    (engine_name, backend, project_id, drawing_id,
     candidate_count, latency_ms, success, error_type)
VALUES (:engine_name, :backend, :project_id, :drawing_id,
        :candidate_count, :latency_ms, :success, :error_type)
"""


def _load_cadtransformer_backend() -> SpottingBackend | None:
    """懒加载 C-08 CADTransformerBackend（可选依赖）。

    未落地 / import 失败 / 实例化失败 → 返回 None（回退 mock），**绝不硬依赖其已存在**，
    绝不在此顶层 import SymPoint / torch。
    """
    try:
        from .cadtransformer.backend import CADTransformerBackend  # type: ignore
    except Exception as exc:  # noqa: BLE001 — 后端未落地/依赖缺失即优雅回退
        logger.info("[spotting] CADTransformer 后端不可用，回退 mock: %s", exc)
        return None
    try:
        return CADTransformerBackend()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[spotting] CADTransformer 实例化失败，回退 mock: %s", exc)
        return None


def _default_backends() -> list[SpottingBackend]:
    """默认后端优先级链：CADTransformer(可用则优先) → Mock(离线兜底)。"""
    backends: list[SpottingBackend] = []
    cad = _load_cadtransformer_backend()
    if cad is not None:
        backends.append(cad)
    backends.append(MockSpottingBackend())
    return backends


class SpottingService:
    """符号 spotting 推理服务。

    - ``db``：可选，注入后异步记录调用日志（无 db 或无事件循环时静默跳过）。
    - ``backends``：可选，按序探测 ``is_available()`` 选首个可用后端；缺省用默认链。
    """

    def __init__(self, db=None, backends: list[SpottingBackend] | None = None):
        self._db = db
        self._backends: list[SpottingBackend] = (
            list(backends) if backends is not None else _default_backends()
        )

    # ──────────────────────────── public ────────────────────────────

    def list_backends(self) -> list[dict]:
        """后端清单与可用性（供 ops/管理后台观测选路结果）。"""
        out: list[dict] = []
        for backend in self._backends:
            out.append({"name": backend.name, "available": self._safe_available(backend)})
        return out

    def select_backend(self) -> SpottingBackend:
        """按序返回首个可用后端；全不可用则兜底 MockSpottingBackend。"""
        for backend in self._backends:
            if self._safe_available(backend):
                return backend
        logger.warning("[spotting] 无可用后端，兜底 MockSpottingBackend")
        return MockSpottingBackend()

    def spot_doc(
        self,
        doc: PrimitiveDoc,
        *,
        project_id: str | None = None,
        drawing_id: str | None = None,
    ) -> SpottingResult:
        """图元文档 → 符号候选。选后端→spot→异步记日志，优雅降级绝不抛异常。"""
        backend = self.select_backend()
        start = time.perf_counter()
        try:
            result = backend.spot(doc)
        except Exception as exc:  # noqa: BLE001 — 后端异常跨边界降级
            latency_ms = int((time.perf_counter() - start) * 1000)
            logger.error("[spotting] 后端 %s 推理异常: %s", backend.name, exc)
            self._record_log(
                backend=backend.name, candidate_count=0, latency_ms=latency_ms,
                success=False, error=str(exc), project_id=project_id, drawing_id=drawing_id,
            )
            return SpottingResult(backend=backend.name, warnings=(f"后端异常降级: {exc}",))

        latency_ms = int((time.perf_counter() - start) * 1000)
        self._record_log(
            backend=result.backend or backend.name,
            candidate_count=len(result.candidates), latency_ms=latency_ms,
            success=True, error=None, project_id=project_id, drawing_id=drawing_id,
        )
        return result

    def spot_drawing(
        self,
        data: bytes,
        file_ext: str,
        *,
        project_id: str | None = None,
        drawing_id: str | None = None,
    ) -> SpottingResult:
        """原始字节（DXF/DWG/PDF）→ 预处理 → 符号候选。预处理失败亦优雅降级。"""
        try:
            pre = preprocess_drawing(data, file_ext)
        except Exception as exc:  # noqa: BLE001 — 预处理失败降级
            logger.error("[spotting] 预处理失败(%s): %s", file_ext, exc)
            return SpottingResult(backend="", warnings=(f"预处理失败降级: {exc}",))

        result = self.spot_doc(pre.doc, project_id=project_id, drawing_id=drawing_id)
        pre_warnings = pre.doc.warnings
        if pre_warnings:
            result = SpottingResult(
                candidates=result.candidates,
                backend=result.backend,
                warnings=result.warnings + pre_warnings,
            )
        return result

    # ──────────────────────────── private ───────────────────────────

    @staticmethod
    def _safe_available(backend: SpottingBackend) -> bool:
        try:
            return bool(backend.is_available())
        except Exception as exc:  # noqa: BLE001
            logger.warning("[spotting] 后端 %s 可用性探测异常: %s", getattr(backend, "name", "?"), exc)
            return False

    def _record_log(
        self,
        *,
        backend: str,
        candidate_count: int,
        latency_ms: int,
        success: bool,
        error: str | None,
        project_id: str | None,
        drawing_id: str | None,
    ) -> None:
        """异步 fire-and-forget 写调用日志（无 db / 无事件循环则静默跳过，绝不阻塞）。"""
        if self._db is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 同步上下文无事件循环，跳过异步日志
        loop.create_task(
            self._log(
                backend=backend, candidate_count=candidate_count, latency_ms=latency_ms,
                success=success, error=error, project_id=project_id, drawing_id=drawing_id,
            )
        )

    async def _log(
        self,
        *,
        backend: str,
        candidate_count: int,
        latency_ms: int,
        success: bool,
        error: str | None,
        project_id: str | None,
        drawing_id: str | None,
    ) -> None:
        """写 symbol_spotting_logs（仿 router._log；日志失败不影响主流程）。"""
        try:
            await self._db.execute(
                _LOG_INSERT_SQL,
                engine_name=ENGINE_NAME,
                backend=backend,
                project_id=project_id,
                drawing_id=drawing_id,
                candidate_count=int(candidate_count),
                latency_ms=int(latency_ms),
                success=success,
                error_type=error[:200] if error else None,
            )
        except Exception as exc:  # noqa: BLE001 — 日志失败静默
            logger.warning("[spotting] 调用日志写入失败: %s", exc)
