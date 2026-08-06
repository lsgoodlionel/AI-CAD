"""人审任务聚合单测(统一工作台)。"""
import pytest

from services.review_tasks import collect_review_tasks


class _FakeDB:
    """按 SQL 关键字返回计数;可注入异常验证 guarded 行为。"""

    def __init__(self, counts=None, transforms=None, fail=False):
        self.counts = counts or {}
        self.transforms = transforms or []
        self.fail = fail

    async def fetch_one(self, sql, params=None):
        if self.fail:
            raise RuntimeError("db down")
        for key, n in self.counts.items():
            if key in sql:
                return {"n": n}
        return {"n": 0}

    async def fetch_all(self, sql, params=None):
        if self.fail:
            raise RuntimeError("db down")
        return self.transforms


@pytest.mark.asyncio
async def test_tasks_cover_all_review_entries():
    """各类人审入口全部聚合(解决入口散落找不到的问题)。"""
    tasks = await collect_review_tasks(_FakeDB(), "p1")
    keys = {t["key"] for t in tasks}
    assert keys == {"suspect_scale", "scale_pending", "axis_calibration",
                    "component_review", "story_height"}
    assert all(t["title"] and t["why"] and t["route"] for t in tasks)


@pytest.mark.asyncio
async def test_high_severity_ranked_first():
    """按价值排序:坐标变换类(修一张见效一张)排在前。"""
    tasks = await collect_review_tasks(_FakeDB(), "p1")
    assert tasks[0]["severity"] == "high"
    severities = [t["severity"] for t in tasks]
    assert severities == sorted(severities, key=lambda s: {"high": 0, "medium": 1}[s])


@pytest.mark.asyncio
async def test_counts_are_wired():
    db = _FakeDB(counts={"drawing_extracted_info e": 1310, "component_instances": 1981})
    tasks = {t["key"]: t for t in await collect_review_tasks(db, "p1")}
    assert tasks["scale_pending"]["count"] == 1310
    assert tasks["component_review"]["count"] == 1981


@pytest.mark.asyncio
async def test_suspect_scale_counts_nonstandard_only():
    """仅统计非标准比例尺(实测 1:2815 这类算错值),标准值不计。"""
    db = _FakeDB(transforms=[
        {"scale_m_pt": 0.03528},    # 1:100 标准 → 不计
        {"scale_m_pt": 0.9931},     # ≈1:2815 非标准 → 计
    ])
    tasks = {t["key"]: t for t in await collect_review_tasks(db, "p1")}
    assert tasks["suspect_scale"]["count"] == 1


@pytest.mark.asyncio
async def test_db_failure_degrades_to_zero_not_crash():
    """单项失败记 0,不影响工作台可用(guarded)。"""
    tasks = await collect_review_tasks(_FakeDB(fail=True), "p1")
    assert len(tasks) == 5
    assert all(t["count"] == 0 for t in tasks)
