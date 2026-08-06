"""轴线标定进度单测(标定入口的数据源)。"""
import pytest

from services.axis_calibration_status import (
    SUGGESTED_MIN_AXES, calibration_state, is_plan_title,
    list_calibration_status, prioritize,
)


def test_is_plan_title_matches_plan_drawings_only():
    assert is_plan_title("北区三层墙柱平面图")
    assert is_plan_title("1区基坑降压井平面布置图")
    assert not is_plan_title("剖面图 1-1")
    assert not is_plan_title("图纸目录")
    assert not is_plan_title(None)


def test_calibration_state_thresholds():
    assert calibration_state(0) == "none"
    assert calibration_state(1) == "partial"
    assert calibration_state(SUGGESTED_MIN_AXES - 1) == "partial"
    assert calibration_state(SUGGESTED_MIN_AXES) == "ready"


def test_prioritize_puts_uncalibrated_plans_first():
    rows = [
        {"drawing_no": "C", "title": "三层平面图", "axis_count": 6},
        {"drawing_no": "A", "title": "剖面图", "axis_count": 0},
        {"drawing_no": "B", "title": "二层平面图", "axis_count": 0},
        {"drawing_no": "D", "title": "四层平面图", "axis_count": 2},
    ]
    ordered = prioritize(rows)
    # 未标定的平面图最前 → 未标定的非平面图 → 部分标定 → 已就绪
    assert [r["drawing_no"] for r in ordered] == ["B", "A", "D", "C"]


def test_prioritize_does_not_mutate_input():
    rows = [{"drawing_no": "Z", "title": "平面图", "axis_count": 9},
            {"drawing_no": "A", "title": "平面图", "axis_count": 0}]
    prioritize(rows)
    assert rows[0]["drawing_no"] == "Z"


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    async def fetch_all(self, sql, params):
        return self._rows


@pytest.mark.asyncio
async def test_list_calibration_status_filters_plans_and_labels_state():
    db = _FakeDb([
        {"drawing_id": "d1", "drawing_no": "S-1", "title": "二层平面图",
         "discipline": "structure", "axis_count": 0},
        {"drawing_id": "d2", "drawing_no": "S-2", "title": "剖面图",
         "discipline": "structure", "axis_count": 0},
        {"drawing_id": "d3", "drawing_no": "S-3", "title": "三层平面图",
         "discipline": "structure", "axis_count": 5},
    ])
    res = await list_calibration_status(db, "p1")
    assert res["total"] == 2                       # 剖面图被平面图过滤剔除
    assert [i["drawing_no"] for i in res["items"]] == ["S-1", "S-3"]
    assert [i["state"] for i in res["items"]] == ["none", "ready"]


@pytest.mark.asyncio
async def test_list_calibration_status_can_include_all_views():
    db = _FakeDb([
        {"drawing_id": "d1", "drawing_no": "S-1", "title": "剖面图",
         "discipline": "structure", "axis_count": 0},
    ])
    res = await list_calibration_status(db, "p1", plan_only=False)
    assert res["total"] == 1


@pytest.mark.asyncio
async def test_list_calibration_status_paginates():
    rows = [{"drawing_id": f"d{i}", "drawing_no": f"S-{i:02d}",
             "title": "平面图", "discipline": "structure", "axis_count": 0}
            for i in range(25)]
    res = await list_calibration_status(_FakeDb(rows), "p1", page=2, page_size=10)
    assert res["total"] == 25 and len(res["items"]) == 10
    assert res["items"][0]["drawing_no"] == "S-10"
