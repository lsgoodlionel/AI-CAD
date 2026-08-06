"""僵尸构建检测单测(实测:进度停在 2308/2309 而 worker 无活跃任务)。"""
from datetime import datetime, timedelta, timezone

from services.build_health import build_health, is_stale_building


def _t(minutes_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def test_stale_when_building_without_progress():
    assert is_stale_building("building", _t(25)) is True     # 实测卡了 25 分钟


def test_not_stale_when_recently_updated():
    assert is_stale_building("building", _t(2)) is False


def test_non_building_never_stale():
    assert is_stale_building("ready", _t(999)) is False
    assert is_stale_building("failed", _t(999)) is False


def test_missing_timestamp_not_stale():
    assert is_stale_building("building", None) is False


def test_naive_datetime_treated_as_utc():
    naive = (datetime.now(timezone.utc) - timedelta(minutes=30)).replace(tzinfo=None)
    assert is_stale_building("building", naive) is True


def test_health_hint_includes_progress():
    h = build_health("building", _t(30), {"done": 2308, "total": 2309})
    assert h["stale"] is True
    assert "2308/2309" in h["hint"]
    assert "重新生成模型" in h["hint"]


def test_health_no_hint_when_healthy():
    h = build_health("ready", _t(1), None)
    assert h["stale"] is False and h["hint"] == ""
