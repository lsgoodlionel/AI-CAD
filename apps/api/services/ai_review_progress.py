"""AI review progress formatting helpers."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any


STAGE_DEFS = [
    {
        "key": "queued",
        "name": "排队启动",
        "description": "任务已创建，等待审图服务接收",
        "weight": 5,
    },
    {
        "key": "prepare",
        "name": "读取图纸信息",
        "description": "读取图纸元数据、文件位置和项目上下文",
        "weight": 10,
    },
    {
        "key": "vision",
        "name": "图纸解析/OCR",
        "description": "解析图纸文本、图层和可识别构件信息",
        "weight": 30,
    },
    {
        "key": "rules",
        "name": "规则审查",
        "description": "按内置强条和专业规则进行快速校验",
        "weight": 18,
    },
    {
        "key": "kg",
        "name": "知识图谱审查",
        "description": "结合规范知识图谱做条文关联和冲突分析",
        "weight": 14,
    },
    {
        "key": "rag",
        "name": "规范检索审查",
        "description": "检索规范条文并进行上下文校验",
        "weight": 18,
    },
    {
        "key": "summary",
        "name": "汇总报告",
        "description": "合并问题、排序严重程度并生成审图摘要",
        "weight": 5,
    },
]

STAGE_MAP = {stage["key"]: stage for stage in STAGE_DEFS}


def _to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def estimate_total_seconds(file_size_kb: int | None = None, recent_avg_ms: int | None = None) -> int:
    """Estimate review duration from recent model speed, with file size as a coarse fallback."""
    if recent_avg_ms and recent_avg_ms > 0:
        return max(45, min(900, int(recent_avg_ms / 1000 * 1.25)))
    size_mb = max(1, int((file_size_kb or 0) / 1024))
    return max(90, min(900, 90 + size_mb * 6))


def progress_payload(
    *,
    status: str,
    stage_key: str,
    started_at: datetime | None,
    updated_at: datetime | None = None,
    completed_keys: list[str] | None = None,
    active_keys: list[str] | None = None,
    pending_keys: list[str] | None = None,
    estimated_total_seconds: int | None = None,
    warnings: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    completed = completed_keys or []
    active = active_keys or ([stage_key] if stage_key not in completed else [])
    pending = pending_keys
    if pending is None:
        finished_or_active = set(completed + active)
        pending = [stage["key"] for stage in STAGE_DEFS if stage["key"] not in finished_or_active]

    percent = sum(STAGE_MAP[key]["weight"] for key in completed if key in STAGE_MAP)
    if status == "done":
        percent = 100
        active = []
        pending = []
    elif status == "failed":
        percent = max(percent, 100)
        active = []
    elif active:
        percent += max(2, int(sum(STAGE_MAP[key]["weight"] for key in active if key in STAGE_MAP) * 0.35))
        percent = min(percent, 95)

    now = updated_at or datetime.now(timezone.utc)
    elapsed = int((now - started_at).total_seconds()) if started_at else 0
    total_estimate = estimated_total_seconds or estimate_total_seconds()
    remaining = 0 if status in {"done", "failed"} else max(0, total_estimate - elapsed)

    if status == "processing" and elapsed > total_estimate * 2:
        warnings = [*(warnings or []), "当前耗时已明显超过预估，可能存在模型调用阻塞或任务卡住"]

    current_stage = STAGE_MAP.get(stage_key, STAGE_MAP["queued"])
    return {
        "status": status,
        "percent": percent,
        "stage_key": stage_key,
        "stage_name": current_stage["name"],
        "stage_description": current_stage["description"],
        "started_at": _iso(started_at),
        "updated_at": _iso(now),
        "elapsed_seconds": elapsed,
        "estimated_total_seconds": total_estimate,
        "estimated_remaining_seconds": remaining,
        "completed_parts": [STAGE_MAP[key] for key in completed if key in STAGE_MAP],
        "active_parts": [STAGE_MAP[key] for key in active if key in STAGE_MAP],
        "pending_parts": [STAGE_MAP[key] for key in pending if key in STAGE_MAP],
        "warnings": warnings or [],
        "metrics": metrics or {},
    }


def normalize_report_progress(report: dict[str, Any], file_size_kb: int | None = None) -> dict[str, Any]:
    """Return a UI-ready progress object for both new and legacy reports."""
    engine_results = report.get("engine_results") or {}
    if isinstance(engine_results, str):
        try:
            engine_results = json.loads(engine_results)
        except json.JSONDecodeError:
            engine_results = {}
    raw_progress = engine_results.get("progress") if isinstance(engine_results, dict) else None
    started_at = _to_datetime(report.get("created_at"))
    completed_at = _to_datetime(report.get("completed_at"))
    status = report.get("status") or "pending"

    if isinstance(raw_progress, dict):
        progress = dict(raw_progress)
        progress.setdefault("status", status)
        if status == "done":
            progress["status"] = "done"
            progress["percent"] = 100
            processing_ms = (progress.get("metrics") or {}).get("processing_ms") or report.get("processing_ms")
            if processing_ms:
                progress["elapsed_seconds"] = max(1, int(processing_ms / 1000))
                progress["estimated_total_seconds"] = progress["elapsed_seconds"]
            progress["estimated_remaining_seconds"] = 0
            progress["active_parts"] = []
            progress["pending_parts"] = []
            progress.setdefault("completed_parts", [stage for stage in STAGE_DEFS])
            progress.setdefault("updated_at", _iso(completed_at))
            progress.setdefault("metrics", {})
            return progress
        if status == "failed":
            progress["status"] = "failed"
            progress["warnings"] = [*(progress.get("warnings") or []), "AI 审图任务失败，请查看后端日志或重新上传触发"]
        return progress

    if status == "done":
        return progress_payload(
            status="done",
            stage_key="summary",
            started_at=started_at,
            updated_at=completed_at,
            completed_keys=[stage["key"] for stage in STAGE_DEFS],
            estimated_total_seconds=max(1, int((report.get("processing_ms") or 0) / 1000)),
        )

    if status == "failed":
        return progress_payload(
            status="failed",
            stage_key="summary",
            started_at=started_at,
            updated_at=completed_at,
            completed_keys=[],
            pending_keys=[],
            warnings=["AI 审图任务失败，请查看后端日志或重新上传触发"],
        )

    return progress_payload(
        status=status,
        stage_key="queued",
        started_at=started_at,
        completed_keys=[],
        active_keys=["queued"],
        estimated_total_seconds=estimate_total_seconds(file_size_kb),
        warnings=["该任务来自旧版本或尚未写入阶段进度，正在等待审图服务更新"],
    )
