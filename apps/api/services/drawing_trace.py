"""图纸正向追溯(Phase G1):一张图识别了什么 + 用在哪(生成了哪些模型构件)。

model_usage_from_scene 纯函数:从 project_models.scene 统计某图 src 的构件
(按楼层/类别),支撑「正向追溯」——图纸 → 模型用途。
"""
from __future__ import annotations

from typing import Any

_ELEMENT_KINDS = ("columns", "walls", "beams", "slabs", "pipes", "equipment")


def model_usage_from_scene(scene: dict, drawing_id: str) -> dict:
    """从 scene 统计 src=drawing_id 的构件用途。

    返回 {used, total_elements, floors:[{key,label,by_kind:{kind:count}}], generated_at}。
    """
    floors_out: list[dict] = []
    total = 0
    for floor in (scene or {}).get("floors", []) or []:
        elements = floor.get("elements") or {}
        by_kind: dict[str, int] = {}
        for kind in _ELEMENT_KINDS:
            n = sum(
                1 for el in (elements.get(kind) or [])
                if str(el.get("src") or "") == str(drawing_id)
            )
            if n:
                by_kind[kind] = n
        if by_kind:
            floor_total = sum(by_kind.values())
            total += floor_total
            floors_out.append({
                "key": floor.get("key"),
                "label": floor.get("label") or floor.get("key"),
                "by_kind": by_kind,
                "count": floor_total,
            })
    return {
        "used": total > 0,
        "total_elements": total,
        "floors": floors_out,
        "generated_at": (scene or {}).get("generated_at"),
    }


# ── DB 读取 ─────────────────────────────────────────────────────

_SCENE_SQL = "SELECT version, scene FROM project_models WHERE project_id = :project_id"
_DRAWING_SQL = (
    "SELECT id, project_id, drawing_no, title, discipline FROM drawings WHERE id = :id"
)


def _parse_scene(value: Any) -> dict:
    import json
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value or {}


async def build_drawing_trace(db: Any, drawing_id: str) -> dict | None:
    """一张图的完整追溯:基本信息 + 识别信息按类别汇总 + 模型用途。"""
    drawing = await db.fetch_one(_DRAWING_SQL, {"id": drawing_id})
    if drawing is None:
        return None
    drawing = dict(drawing)
    project_id = str(drawing["project_id"])

    # 识别信息按类别/抽取器汇总(生效值)
    from services.drawing_archive import fetch_drawing_archive
    items = await fetch_drawing_archive(db, drawing_id)
    by_category: dict[str, int] = {}
    by_extractor: dict[str, int] = {}
    for it in items:
        by_category[it["category"]] = by_category.get(it["category"], 0) + 1
        by_extractor[it["extractor"]] = by_extractor.get(it["extractor"], 0) + 1

    # 模型用途(从最新 scene 统计 src=该图的构件)
    row = await db.fetch_one(_SCENE_SQL, {"project_id": project_id})
    usage = {"used": False, "total_elements": 0, "floors": [], "model_version": None}
    if row is not None:
        scene = _parse_scene(row["scene"])
        u = model_usage_from_scene(scene, drawing_id)
        u["model_version"] = row["version"]
        usage = u

    return {
        "drawing": {
            "id": str(drawing["id"]), "drawing_no": drawing["drawing_no"],
            "title": drawing["title"], "discipline": drawing["discipline"],
        },
        "info": {
            "total": len(items),
            "by_category": by_category,
            "by_extractor": by_extractor,
        },
        "model_usage": usage,
    }
