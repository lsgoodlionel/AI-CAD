"""跨图分析 analyze_batch 测试（FakeDB 喂数据，纯聚合逻辑）"""
import json
from unittest.mock import AsyncMock

import pytest

from core.ai_review.cross_drawing import analyze_batch

PROJECT_ID = "22222222-2222-2222-2222-222222222222"


class _FakeDB:
    """两次 fetch_all：第一次返回图纸行，第二次返回问题行"""

    def __init__(self, drawings: list[dict], issues: list[dict]):
        self.fetch_all = AsyncMock(side_effect=[drawings, issues])


def _drawing(did: str, no: str, version: str = "A", discipline: str = "structure") -> dict:
    return {"id": did, "drawing_no": no, "version": version, "discipline": discipline}


def _issue(no: str, discipline: str = "structure", severity: str = "major", **extra) -> dict:
    base = {
        "drawing_id": f"id-{no}",
        "drawing_no": no,
        "discipline": discipline,
        "severity": severity,
        "location_json": None,
        "interface_related": None,
        "review_method": None,
    }
    base.update(extra)
    return base


# ── 重复图号 / 版本冲突 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_detects_duplicate_drawing_no():
    db = _FakeDB(
        [_drawing("d1", "JG-01"), _drawing("d2", "JG-01"), _drawing("d3", "JG-02")],
        [],
    )

    result = await analyze_batch(db, PROJECT_ID, ["d1", "d2", "d3"])

    assert result["重复图号"] == [{"drawing_no": "JG-01", "drawing_ids": ["d1", "d2"]}]


@pytest.mark.asyncio
async def test_detects_version_conflict():
    db = _FakeDB(
        [_drawing("d1", "JG-01", version="A"), _drawing("d2", "JG-01", version="B")],
        [],
    )

    result = await analyze_batch(db, PROJECT_ID, ["d1", "d2"])

    assert result["版本冲突"] == [{"drawing_no": "JG-01", "versions": ["A", "B"]}]


@pytest.mark.asyncio
async def test_no_duplicates_for_distinct_drawing_nos():
    db = _FakeDB([_drawing("d1", "JG-01"), _drawing("d2", "JG-02")], [])

    result = await analyze_batch(db, PROJECT_ID, ["d1", "d2"])

    assert result["重复图号"] == []
    assert result["版本冲突"] == []


# ── 接口缺图 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_interface_discipline_is_reported():
    # 套图内只有结构图，issue 指向给排水（mep）→ 接口缺图
    db = _FakeDB(
        [_drawing("d1", "JG-01", discipline="structure")],
        [_issue("JG-01", interface_related=json.dumps(["给排水"]))],
    )

    result = await analyze_batch(db, PROJECT_ID, ["d1"])

    assert result["接口缺图"] == [
        {
            "missing_discipline": "mep",
            "referenced_by": [{"drawing_no": "JG-01", "interface": "给排水"}],
        }
    ]


@pytest.mark.asyncio
async def test_present_interface_discipline_is_not_reported():
    # 接口指向结构，而套图内已有结构图 → 不缺图
    db = _FakeDB(
        [_drawing("d1", "JG-01", discipline="structure")],
        [_issue("JG-01", interface_related=["结构", "围护"])],
    )

    result = await analyze_batch(db, PROJECT_ID, ["d1"])

    assert result["接口缺图"] == []


@pytest.mark.asyncio
async def test_unknown_interface_name_is_ignored():
    db = _FakeDB(
        [_drawing("d1", "JG-01", discipline="structure")],
        [_issue("JG-01", interface_related=["外星专业"])],
    )

    result = await analyze_batch(db, PROJECT_ID, ["d1"])

    assert result["接口缺图"] == []


# ── 问题聚类 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clusters_issues_sharing_level_and_axis_across_drawings():
    location = json.dumps({"levels": ["3F"], "axes": ["A-1"]})
    db = _FakeDB(
        [
            _drawing("d1", "JG-01", discipline="structure"),
            _drawing("d2", "JZ-01", discipline="architecture"),
        ],
        [
            _issue("JG-01", discipline="structure", location_json=location),
            _issue("JZ-01", discipline="architecture", location_json=location),
            _issue("JG-01", discipline="structure",
                   location_json=json.dumps({"levels": ["5F"], "axes": []})),
        ],
    )

    result = await analyze_batch(db, PROJECT_ID, ["d1", "d2"])

    # 仅 ≥2 张图共有的定位 key 会被聚类；5F 只出现在 1 张图上
    assert len(result["问题聚类"]) == 1
    cluster = result["问题聚类"][0]
    assert cluster["count"] == 2
    assert sorted(cluster["drawings"]) == ["JG-01", "JZ-01"]
    assert sorted(cluster["disciplines"]) == ["architecture", "structure"]


@pytest.mark.asyncio
async def test_issues_without_location_are_not_clustered():
    db = _FakeDB(
        [_drawing("d1", "JG-01"), _drawing("d2", "JZ-01")],
        [_issue("JG-01"), _issue("JZ-01", location_json=json.dumps({"levels": [], "axes": []}))],
    )

    result = await analyze_batch(db, PROJECT_ID, ["d1", "d2"])

    assert result["问题聚类"] == []


# ── 高频对象聚合 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_aggregates_priority_objects_from_review_method():
    method = json.dumps({"优先对象": [{"name": "梁", "weight": 3, "hit": True}]})
    db = _FakeDB(
        [_drawing("d1", "JG-01")],
        [
            _issue("JG-01", review_method=method),
            _issue("JG-01", review_method={"优先对象": [{"name": "梁"}, {"name": "洞口"}]}),
        ],
    )

    result = await analyze_batch(db, PROJECT_ID, ["d1"])

    assert result["高频对象聚合"] == [{"name": "梁", "count": 2}, {"name": "洞口", "count": 1}]


# ── 严重度 / 专业分布 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_severity_and_discipline_distributions():
    db = _FakeDB(
        [
            _drawing("d1", "JG-01", discipline="structure"),
            _drawing("d2", "SD-01", discipline="mep"),
        ],
        [
            _issue("JG-01", discipline="structure", severity="critical"),
            _issue("JG-01", discipline="structure", severity="major"),
            _issue("SD-01", discipline="mep", severity="info"),
        ],
    )

    result = await analyze_batch(db, PROJECT_ID, ["d1", "d2"])

    assert result["严重度分布"] == {"critical": 1, "major": 1, "minor": 0, "info": 1}
    assert result["专业分布"] == {"structure": 2, "mep": 1}


# ── 边界：空集合 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_drawing_ids_returns_zeroed_structure():
    db = _FakeDB([], [])

    result = await analyze_batch(db, PROJECT_ID, [])

    assert result["重复图号"] == []
    assert result["版本冲突"] == []
    assert result["接口缺图"] == []
    assert result["问题聚类"] == []
    assert result["高频对象聚合"] == []
    assert result["严重度分布"] == {"critical": 0, "major": 0, "minor": 0, "info": 0}
    assert result["专业分布"] == {}
    # 空集合不应触碰数据库
    db.fetch_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_jsonb_strings_are_ignored():
    db = _FakeDB(
        [_drawing("d1", "JG-01")],
        [_issue("JG-01", location_json="{broken", interface_related="not-json", review_method="[]")],
    )

    result = await analyze_batch(db, PROJECT_ID, ["d1"])

    assert result["问题聚类"] == []
    assert result["接口缺图"] == []
    assert result["高频对象聚合"] == []
