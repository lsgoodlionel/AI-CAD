"""轴网识别结果的读写(migration 041)。

**为什么分两张表**:识别结果可以随时重跑覆盖,但**人工确认过的分区编号不能被冲掉**
——§8.0.5 未规定哪个分区是 1,这是人给的信息,重抽一次就丢掉等于白确认。
这与 Phase E1.5 档案层「auto / verified 分离」是同一条经验。
"""
from __future__ import annotations

import json
from typing import Any

_UPSERT_SQL = """
INSERT INTO axis_recognition
    (drawing_id, project_id, status, page_w, page_h, circle_count,
     additional_count, axis_count, zones, axes, anchors, outliers,
     violations, transform, error, suspect_symbol_field, warnings,
     leader_count, is_split_view, split_view_numbering, updated_at)
VALUES (CAST(:drawing_id AS uuid), CAST(:project_id AS uuid), :status,
        :page_w, :page_h, :circle_count, :additional_count, :axis_count,
        CAST(:zones AS jsonb), CAST(:axes AS jsonb), CAST(:anchors AS jsonb),
        CAST(:outliers AS jsonb), CAST(:violations AS jsonb),
        CAST(:transform AS jsonb), :error, :suspect_symbol_field,
        CAST(:warnings AS jsonb), :leader_count, :is_split_view,
        CAST(:split_view_numbering AS jsonb), now())
ON CONFLICT (drawing_id) DO UPDATE SET
    status = EXCLUDED.status, page_w = EXCLUDED.page_w, page_h = EXCLUDED.page_h,
    circle_count = EXCLUDED.circle_count,
    additional_count = EXCLUDED.additional_count,
    axis_count = EXCLUDED.axis_count, zones = EXCLUDED.zones,
    axes = EXCLUDED.axes, anchors = EXCLUDED.anchors,
    outliers = EXCLUDED.outliers, violations = EXCLUDED.violations,
    transform = EXCLUDED.transform, error = EXCLUDED.error,
    suspect_symbol_field = EXCLUDED.suspect_symbol_field,
    warnings = EXCLUDED.warnings, leader_count = EXCLUDED.leader_count,
    is_split_view = EXCLUDED.is_split_view,
    split_view_numbering = EXCLUDED.split_view_numbering,
    updated_at = now()
"""

_FETCH_SQL = """
SELECT drawing_id, project_id, status, page_w, page_h, circle_count,
       additional_count, axis_count, zones, axes, anchors, outliers,
       violations, transform, error, suspect_symbol_field, warnings,
       leader_count, is_split_view, split_view_numbering, updated_at
FROM axis_recognition WHERE drawing_id = CAST(:drawing_id AS uuid)
"""

_FETCH_PROJECT_SQL = """
SELECT r.drawing_id, d.drawing_no, d.title, r.status, r.axis_count,
       r.additional_count, r.transform, r.updated_at,
       jsonb_array_length(COALESCE(r.zones, '[]'::jsonb))      AS zone_count,
       jsonb_array_length(COALESCE(r.anchors, '[]'::jsonb))    AS anchor_count,
       jsonb_array_length(COALESCE(r.outliers, '[]'::jsonb))   AS outlier_count,
       jsonb_array_length(COALESCE(r.violations, '[]'::jsonb)) AS violation_count
FROM axis_recognition r
JOIN drawings d ON d.id = r.drawing_id
WHERE r.project_id = CAST(:project_id AS uuid)
ORDER BY r.updated_at DESC
"""

_CONFIRM_SQL = """
INSERT INTO axis_zone_confirmation
    (project_id, drawing_id, zone_index, zone_label, confirmed_by)
VALUES (CAST(:project_id AS uuid), CAST(:drawing_id AS uuid),
        :zone_index, :zone_label, CAST(:confirmed_by AS uuid))
ON CONFLICT (drawing_id, zone_index) DO UPDATE SET
    zone_label = EXCLUDED.zone_label,
    confirmed_by = EXCLUDED.confirmed_by,
    confirmed_at = now()
"""

_FETCH_CONFIRMATIONS_SQL = """
SELECT zone_index, zone_label FROM axis_zone_confirmation
WHERE drawing_id = CAST(:drawing_id AS uuid)
"""

_JSON_FIELDS = ("zones", "axes", "anchors", "outliers", "violations",
                "transform", "warnings", "split_view_numbering")


def _loads(value: Any) -> Any:
    """驱动可能返回 str 或已解析对象,两种都要吃得下。"""
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


async def save_result(db: Any, *, project_id: str, drawing_id: str,
                      result: dict, status: str = "ready",
                      error: str | None = None) -> None:
    """落库识别结果(幂等 upsert)。"""
    await db.execute(_UPSERT_SQL, {
        "drawing_id": drawing_id, "project_id": project_id, "status": status,
        "page_w": result.get("page_w"), "page_h": result.get("page_h"),
        "circle_count": result.get("circle_count", 0),
        "additional_count": result.get("additional_count", 0),
        "axis_count": result.get("axis_count", 0),
        "error": error,
        # 可疑标记必须落库 —— 只在内存里打标记等于没打(消费方读不到)
        "suspect_symbol_field": bool(result.get("suspect_symbol_field")),
        # 坐标标注引线数 —— drawing_role 第 1 级判据的关键证据(migration 043)
        "leader_count": int(result.get("leader_count") or 0),
        # 分幅标记(migration 044):分幅无分区号,不进人工队列
        "is_split_view": bool(result.get("is_split_view")),
        # **值为 None 时要绑 SQL NULL,不能绑 JSON 'null'**:
        # `json.dumps(None)` 得到字符串 "null",CAST 成 jsonb 后是 JSON null,
        # 于是 `transform IS NOT NULL` 对所有行都为真 —— 实测 2309 行里
        # 2187 行是这种假非空,真有变换的只有 122 张,消费方会被骗。
        **{f: (None if result.get(f) is None
               else json.dumps(result[f], ensure_ascii=False))
           for f in _JSON_FIELDS},
    })


async def fetch_result(db: Any, drawing_id: str) -> dict | None:
    row = await db.fetch_one(_FETCH_SQL, {"drawing_id": drawing_id})
    if row is None:
        return None
    out = dict(row)
    for field in _JSON_FIELDS:
        out[field] = _loads(out.get(field))
    out["drawing_id"] = str(out["drawing_id"])
    out["project_id"] = str(out["project_id"])
    return out


async def fetch_project_summary(db: Any, project_id: str) -> list[dict]:
    """项目内每图一行的摘要 —— 用于列出「有多少事等人处理」。"""
    rows = await db.fetch_all(_FETCH_PROJECT_SQL, {"project_id": project_id})
    out = []
    for r in rows:
        item = dict(r)
        item["drawing_id"] = str(item["drawing_id"])
        item["transform"] = _loads(item.get("transform"))
        out.append(item)
    return out


async def confirm_zone(db: Any, *, project_id: str, drawing_id: str,
                       zone_index: int, zone_label: str,
                       confirmed_by: str | None) -> None:
    """记下人工确认的分区编号(同图同分区幂等覆盖)。"""
    await db.execute(_CONFIRM_SQL, {
        "project_id": project_id, "drawing_id": drawing_id,
        "zone_index": int(zone_index), "zone_label": str(zone_label).strip(),
        "confirmed_by": confirmed_by})


async def fetch_zone_labels(db: Any, drawing_id: str) -> dict[int, str]:
    """已确认的 {分区下标: 分区号};重跑识别时带上,确认结果不被冲掉。"""
    rows = await db.fetch_all(_FETCH_CONFIRMATIONS_SQL,
                              {"drawing_id": drawing_id})
    return {int(r["zone_index"]): r["zone_label"] for r in rows}
