"""交叉点与工程坐标原点仓储单测。"""
import pytest

from services.axis_intersection_repo import (
    delete_intersection, fetch_drawing_intersections, fetch_project_intersections,
    list_origins, save_intersection, set_origin,
)


class _FakeDb:
    def __init__(self, rows=None, origins=None, disciplines=None):
        self.rows = rows or []
        self.origins = origins or []
        self.disciplines = disciplines or []
        self.calls: list[tuple[str, dict]] = []

    async def fetch_one(self, sql, params):
        self.calls.append(("one", params))
        return {"id": "i1"}

    async def fetch_all(self, sql, params):
        self.calls.append(("all", params))
        if "project_coordinate_origins" in sql:
            return self.origins
        if "discipline_label AS discipline" in sql:
            return self.disciplines
        return self.rows

    async def execute(self, sql, params):
        self.calls.append(("exec", params))


@pytest.mark.asyncio
async def test_save_intersection_passes_labels_and_world_coords():
    db = _FakeDb()
    got = await save_intersection(
        db, project_id="p1", drawing_id="d1",
        point={"label_x": " 1 ", "label_y": "A", "x_norm": 0.3, "y_norm": 0.4,
               "world_x": 12.5, "world_y": -3.0, "world_z": 0.0},
        created_by="u1")
    assert got == "i1"
    sent = db.calls[0][1]
    assert sent["label_x"] == "1"          # 去空白,免得 " 1 " 与 "1" 成两个点
    assert sent["wx"] == 12.5 and sent["wz"] == 0.0


@pytest.mark.asyncio
async def test_fetch_drawing_intersections_casts_numeric_fields():
    db = _FakeDb(rows=[{"id": "i1", "label_x": "1", "label_y": "A",
                        "x_norm": 0.25, "y_norm": 0.75,
                        "world_x": None, "world_y": None, "world_z": None,
                        "note": None}])
    got = await fetch_drawing_intersections(db, "d1")
    assert got[0]["x_norm"] == 0.25 and got[0]["world_x"] is None


@pytest.mark.asyncio
async def test_fetch_project_intersections_groups_by_drawing():
    db = _FakeDb(rows=[
        {"drawing_id": "d1", "id": "i1", "label_x": "1", "label_y": "A",
         "x_norm": 0.1, "y_norm": 0.2, "world_x": None, "world_y": None, "world_z": None},
        {"drawing_id": "d2", "id": "i2", "label_x": "1", "label_y": "A",
         "x_norm": 0.3, "y_norm": 0.4, "world_x": None, "world_y": None, "world_z": None},
    ])
    got = await fetch_project_intersections(db, "p1")
    assert set(got) == {"d1", "d2"} and len(got["d1"]) == 1


@pytest.mark.asyncio
async def test_delete_intersection_targets_label_pair():
    db = _FakeDb()
    await delete_intersection(db, "d1", "1", "A")
    assert db.calls[0][1] == {"drawing_id": "d1", "label_x": "1", "label_y": "A"}


@pytest.mark.asyncio
async def test_list_origins_reports_disciplines_still_missing():
    """缺一个专业的原点就整体错位,必须点名而不是静默。"""
    db = _FakeDb(
        origins=[{"discipline": "结构", "drawing_id": "d1", "intersection_id": "i1",
                  "note": None, "drawing_no": "S-1", "title": "平面",
                  "label_x": "1", "label_y": "A",
                  "world_x": 0.0, "world_y": 0.0, "world_z": 0.0}],
        disciplines=[{"discipline": "结构", "n": 300}, {"discipline": "建筑", "n": 480},
                     {"discipline": "给排水", "n": 220}])
    got = await list_origins(db, "p1")
    assert [o["discipline"] for o in got["origins"]] == ["结构"]
    # 按图纸张数降序:先定义图多的专业收益最大
    assert [m["discipline"] for m in got["missing_disciplines"]] == ["建筑", "给排水"]
    assert got["defined"] == 1 and got["total_disciplines"] == 3


@pytest.mark.asyncio
async def test_set_origin_is_per_discipline():
    db = _FakeDb()
    await set_origin(db, project_id="p1", discipline="建筑", drawing_id="d1",
                     intersection_id="i1", note=None, created_by="u1")
    assert db.calls[0][1]["discipline"] == "建筑"
