"""Finding 表行来源 SQL 下推测试（Phase D · 泳道2 · D-05 性能优化）。

覆盖：
- SQL 片段生成（CASE 由 Python 映射表派生、saving 正则与严重度门一致）
- query_table_source：分页/计数双查询、LIMIT 下推、筛选下推、行→Finding 映射
- 计数聚合 _aggregate_source_counts
- finding_service 侧编排辅助：_merge_and_slice / _merge_counts / _cap_derived /
  overlay 收窄
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services import finding_query as fq
from services import finding_service as fs

PROJECT_ID = "11111111-1111-1111-1111-111111111111"


# ══════════════════════════════════════════════════════════════
# SQL 片段生成（防 SQL ↔ Python 语义漂移）
# ══════════════════════════════════════════════════════════════

def test_sql_case_compiles_python_mapping():
    sql = fq._sql_case("i.severity", {"critical": "critical", "major": "high"}, "medium")
    assert sql == (
        "CASE i.severity WHEN 'critical' THEN 'critical' "
        "WHEN 'major' THEN 'high' ELSE 'medium' END"
    )


def test_engine_severity_case_mirrors_python_map():
    """引擎严重度 CASE 必须由 fs._ENGINE_SEVERITY_MAP 派生，逐档一致。"""
    for native, unified in fs._ENGINE_SEVERITY_MAP.items():
        assert f"WHEN '{native}' THEN '{unified}'" in fq._ENGINE_MAPPED


def test_symbol_default_status_case_mirrors_python_map():
    for native, mapped in fs._SYMBOL_STATUS_DEFAULT.items():
        assert f"WHEN '{native}' THEN '{mapped}'" in fq._SYMBOL_MAPPED


def test_saving_sql_gates_on_high_severity_and_all_keywords():
    assert "severity IN ('critical', 'high')" in fq._SAVING_SQL
    for keyword in fs._SAVING_KEYWORDS:
        assert keyword in fq._SAVING_SQL


def test_severity_rank_sql_orders_critical_first():
    # rank 数值须与 Python fs._SEVERITY_RANK 一致
    for sev, rank in fs._SEVERITY_RANK.items():
        assert f"WHEN '{sev}' THEN {rank}" in fq._SEVERITY_RANK_SQL


# ══════════════════════════════════════════════════════════════
# 计数聚合
# ══════════════════════════════════════════════════════════════

def test_aggregate_source_counts_sums_groups():
    rows = [
        {"severity": "high", "status": "pending", "n": 3, "sn": 2},
        {"severity": "high", "status": "closed", "n": 1, "sn": 0},
        {"severity": "low", "status": "pending", "n": 5, "sn": None},
    ]
    counts = fq._aggregate_source_counts("engine", rows)
    assert counts["total"] == 9
    assert counts["by_source"] == {"engine": 9}
    assert counts["by_severity"] == {"high": 4, "low": 5}
    assert counts["by_status"] == {"pending": 8, "closed": 1}
    assert counts["saving"] == 2


def test_aggregate_source_counts_empty_omits_source():
    counts = fq._aggregate_source_counts("review", [])
    assert counts["total"] == 0
    assert counts["by_source"] == {}  # 与旧 _count_by 一致：0 不入 by_source


# ══════════════════════════════════════════════════════════════
# 行 → Finding 映射
# ══════════════════════════════════════════════════════════════

def test_finding_from_engine_row_shape():
    row = {
        "source_key": "e1", "drawing_id": "d1", "severity": "high", "status": "pending",
        "note": None, "status_updated_at": None,
        "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc), "native_status": "open",
        "category": "消防间距不足", "description": "疏散距离超限", "suggestion": None,
        "location_json": {"levels": ["F1"]}, "saving_flag": True,
    }
    finding = fq._finding_from_engine_row(row, PROJECT_ID)
    assert finding["id"] == "engine:e1"
    assert finding["title"] == "消防间距不足"
    assert finding["description"] == "疏散距离超限"
    assert finding["location"] == {"levels": ["F1"]}
    assert finding["has_saving_potential"] is True


def test_finding_from_review_row_builds_title():
    row = {
        "source_key": "r1", "drawing_id": None, "severity": "high", "status": "pending",
        "note": None, "status_updated_at": None, "created_at": None, "native_status": None,
        "discipline_name": "结构", "object_level": "构件级",
        "standard_question": "锚固长度是否满足？", "location_json": None, "saving_flag": False,
    }
    finding = fq._finding_from_review_row(row, PROJECT_ID)
    assert finding["title"] == "结构会审发现（构件级）"
    assert finding["description"] == "锚固长度是否满足？"
    assert finding["drawing_id"] is None


def test_finding_from_symbol_row_formats_confidence():
    row = {
        "source_key": "7", "drawing_id": "d2", "severity": "high", "status": "pending",
        "note": None, "status_updated_at": None, "created_at": None, "native_status": "pending",
        "category": "door", "mep_system": None, "confidence": 0.4, "saving_flag": False,
    }
    finding = fq._finding_from_symbol_row(row, PROJECT_ID)
    assert finding["title"] == "符号待审：door"
    assert "置信度 0.4" in finding["description"]


# ══════════════════════════════════════════════════════════════
# query_table_source：双查询 + 下推
# ══════════════════════════════════════════════════════════════

def _capture(fake_db, page_rows, count_rows):
    """让 fake_db.fetch_all 按 SQL 区分分页/计数查询，并记录 (query, args)。"""
    captured: list[tuple[str, tuple]] = []

    async def _fetch_all(query, *args):
        captured.append((query, args))
        return count_rows if "GROUP BY" in query else page_rows

    fake_db.fetch_all.side_effect = _fetch_all
    return captured


@pytest.mark.asyncio
async def test_query_table_source_maps_rows_and_counts(fake_db):
    page_rows = [{
        "source_key": "e1", "drawing_id": "d1", "severity": "high", "status": "pending",
        "note": None, "status_updated_at": None, "created_at": None, "native_status": "open",
        "category": "问题", "description": "详情", "suggestion": None,
        "location_json": None, "saving_flag": False,
    }]
    count_rows = [{"severity": "high", "status": "pending", "n": 1, "sn": 0}]
    _capture(fake_db, page_rows, count_rows)

    findings, counts = await fq.query_table_source(fake_db, "engine", PROJECT_ID, top_n=50)

    assert [f["id"] for f in findings] == ["engine:e1"]
    assert counts["total"] == 1
    assert counts["by_source"] == {"engine": 1}


@pytest.mark.asyncio
async def test_query_table_source_pushes_limit_top_n(fake_db):
    captured = _capture(fake_db, [], [])
    await fq.query_table_source(fake_db, "engine", PROJECT_ID, top_n=50)

    page_query, page_args = captured[0]
    count_query, count_args = captured[1]
    # 分页查询下推 LIMIT，参数含 top_n；计数查询是 GROUP BY 且无 LIMIT
    assert "LIMIT" in page_query
    assert 50 in page_args
    assert "GROUP BY" in count_query
    assert "LIMIT" not in count_query


@pytest.mark.asyncio
async def test_query_table_source_no_top_n_omits_limit(fake_db):
    captured = _capture(fake_db, [], [])
    await fq.query_table_source(fake_db, "engine", PROJECT_ID, top_n=None)
    page_query, page_args = captured[0]
    assert "LIMIT" not in page_query
    assert page_args == (PROJECT_ID,)


@pytest.mark.asyncio
async def test_query_table_source_pushes_filters_into_where(fake_db):
    captured = _capture(fake_db, [], [])
    await fq.query_table_source(
        fake_db, "engine", PROJECT_ID,
        severity="high", status="pending", drawing_id="d9", top_n=10,
    )
    page_query, page_args = captured[0]
    assert "severity = $2" in page_query
    assert "status = $3" in page_query
    assert "drawing_id = $4" in page_query
    # 参数顺序：project_id, 过滤值..., top_n
    assert page_args == (PROJECT_ID, "high", "pending", "d9", 10)


@pytest.mark.asyncio
async def test_query_table_source_saving_flag_passes_through(fake_db):
    page_rows = [{
        "source_key": "e9", "drawing_id": None, "severity": "high", "status": "pending",
        "note": None, "status_updated_at": None, "created_at": None, "native_status": "open",
        "category": "钢筋超配", "description": "", "suggestion": None,
        "location_json": None, "saving_flag": True,
    }]
    _capture(fake_db, page_rows, [{"severity": "high", "status": "pending", "n": 1, "sn": 1}])
    findings, counts = await fq.query_table_source(fake_db, "engine", PROJECT_ID, top_n=10)
    assert findings[0]["has_saving_potential"] is True
    assert counts["saving"] == 1


@pytest.mark.asyncio
async def test_query_table_source_rejects_non_table_source(fake_db):
    with pytest.raises(ValueError):
        await fq.query_table_source(fake_db, "cross", PROJECT_ID)


# ══════════════════════════════════════════════════════════════
# finding_service 编排辅助
# ══════════════════════════════════════════════════════════════

def test_merge_and_slice_global_sort_then_window():
    critical = fs_final("symbol", "s1", "critical")
    high_new = fs_final("engine", "e2", "high", created_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
    high_old = fs_final("engine", "e1", "high", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pages = [[high_old, high_new], [critical]]
    out = fs._merge_and_slice(pages, offset=0, limit=2)
    assert [it["id"] for it in out] == ["symbol:s1", "engine:e2"]


def test_merge_and_slice_offset():
    items = [fs_final("engine", f"e{i}", "medium") for i in range(5)]
    out = fs._merge_and_slice([items], offset=2, limit=2)
    assert len(out) == 2


def test_merge_and_slice_limit_zero_returns_all_from_offset():
    items = [fs_final("engine", f"e{i}", "medium") for i in range(5)]
    out = fs._merge_and_slice([items], offset=1, limit=0)
    assert len(out) == 4


def test_merge_counts_accumulates():
    agg = {"total": 0, "saving": 0, "by_source": {}, "by_severity": {}, "by_status": {}}
    fs._merge_counts(agg, fs_counts("engine", total=2, by_severity={"high": 2},
                                    by_status={"pending": 2}, saving=1))
    fs._merge_counts(agg, fs_counts("symbol", total=1, by_severity={"high": 1},
                                    by_status={"pending": 1}, saving=0))
    assert agg["total"] == 3
    assert agg["saving"] == 1
    assert agg["by_source"] == {"engine": 2, "symbol": 1}
    assert agg["by_severity"] == {"high": 3}


def test_cap_derived_truncates_and_keeps_most_severe(caplog):
    orig = fs.MAX_DERIVED_FINDINGS
    fs.MAX_DERIVED_FINDINGS = 2
    try:
        items = [
            fs_final("cross", "c1", "low"),
            fs_final("cross", "c2", "critical"),
            fs_final("cross", "c3", "high"),
        ]
        capped = fs._cap_derived(items)
        assert len(capped) == 2
        assert [it["severity"] for it in capped] == ["critical", "high"]  # 低危被截断
    finally:
        fs.MAX_DERIVED_FINDINGS = orig


def test_cap_derived_under_limit_is_noop():
    items = [fs_final("cross", "c1", "low")]
    assert fs._cap_derived(items) is items


@pytest.mark.asyncio
async def test_fetch_status_overlay_scopes_to_sources(fake_db):
    captured: list[tuple[str, tuple]] = []

    async def _fetch_all(query, *args):
        captured.append((query, args))
        return []

    fake_db.fetch_all.side_effect = _fetch_all
    await fs._fetch_status_overlay(fake_db, PROJECT_ID, sources=["cross", "semantic"])
    query, args = captured[0]
    assert "source = ANY($2)" in query
    assert args == (PROJECT_ID, ["cross", "semantic"])


# ── 本文件内的小工具（构造已 finalize 的 Finding / 计数结构）──────────

def fs_final(source: str, key: str, severity: str, created_at=None) -> dict:
    return {
        "id": f"{source}:{key}", "source": source, "project_id": PROJECT_ID,
        "drawing_id": None, "severity": severity, "title": key, "description": "",
        "status": "pending", "location": None, "note": None, "status_updated_at": None,
        "created_at": created_at, "has_saving_potential": False,
    }


def fs_counts(source: str, *, total: int, by_severity: dict, by_status: dict, saving: int) -> dict:
    return {
        "total": total, "by_source": {source: total} if total else {},
        "by_severity": dict(by_severity), "by_status": dict(by_status), "saving": saving,
    }
