"""图纸信息档案:生效值规则 + 人审 verified 仓储(Phase E1.5)。

档案层是全平台单一真相源。所有下游(工程信息/建模/审图/算量)读「生效值」:
同 (drawing_id, category, 归一化 key) 取 verified 优先,否则 active auto 里
confidence 最高。人审修正写 verified 行 + 置原 auto 行 is_active=false(留痕)。

纯函数(effective_values / normalized_key / build_verify_params)离线可测;
IO(persist_verify / fetch_*)对齐 model_topology 仓储风格。
"""
from __future__ import annotations

import json
from typing import Any


def normalized_key(category: str, content: str, value_json: dict | None) -> str:
    """同一语义信息的归一化 key(供择优去重)。

    标高/尺寸按解析数值归一(不同文本表述同值 → 同 key);其余按 content 去空白。
    """
    if value_json:
        if category == "elevation" and "elevation_m" in value_json:
            return f"elevation:{round(float(value_json['elevation_m']), 3)}"
        if category == "dimension" and "dim_mm" in value_json:
            return f"dimension:{round(float(value_json['dim_mm']), 1)}"
        if category == "axis" and value_json.get("label"):
            return f"axis:{str(value_json['label']).strip()}"
    return f"{category}:{(content or '').strip()}"


def _confidence(row: dict) -> float:
    """verified 视为最高(1.0);auto 用其 confidence(None 当确定性 1.0)。"""
    if row.get("source_kind") == "verified":
        return 2.0  # 恒高于任何 auto
    conf = row.get("confidence")
    return 1.0 if conf is None else float(conf)


def effective_values(rows: list[dict]) -> list[dict]:
    """从档案行集合算生效值:按 (category, 归一化 key) 择优。

    - 排除 is_active=False 的行(被 verified 推翻的 auto);
    - verified 优先,其次 confidence 最高的 active auto。
    输入顺序不敏感;输出保持每组首次出现顺序。
    """
    # verified 行经 supersedes 抑制它修正的 auto 行（即便该 auto 的 is_active
    # 因故未置假,也不让脏值参与择优——人审意图以 supersedes 链接为准）
    superseded = {
        row.get("supersedes") for row in rows
        if row.get("source_kind") == "verified" and row.get("supersedes")
    }
    best: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        if not row.get("is_active", True):
            continue
        if row.get("id") in superseded:
            continue
        key = (row["category"], normalized_key(
            row["category"], row.get("content", ""), row.get("value_json")
        ))
        if key not in best:
            order.append(key)
            best[key] = row
        elif _confidence(row) > _confidence(best[key]):
            best[key] = row
    return [best[k] for k in order]


# ── 人审 verify ──────────────────────────────────────────────────

def build_verify_params(
    *, project_id: str, drawing_id: str, category: str,
    content: str, value_json: dict | None,
    supersedes_id: str | None, reviewer_id: str,
    location_json: dict | None = None,
) -> dict:
    """构造人审修正的落库参数:一条 verified 插入 + 可选置原 auto 行失活。"""
    return {
        "insert": {
            "project_id": project_id,
            "drawing_id": drawing_id,
            "category": category,
            "content": content,
            "value_json": json.dumps(value_json, ensure_ascii=False)
                if value_json is not None else None,
            "location_json": json.dumps(location_json, ensure_ascii=False)
                if location_json is not None else None,
            "extractor": "manual",
            "confidence": None,
            "source_kind": "verified",
            "is_active": True,
            "supersedes": supersedes_id,
            "reviewed_by": reviewer_id,
        },
        "deactivate_id": supersedes_id,
    }


_DEACTIVATE_SQL = """
UPDATE drawing_extracted_info
SET is_active = false
WHERE id = :id AND source_kind = 'auto'
"""

_INSERT_VERIFIED_SQL = """
INSERT INTO drawing_extracted_info (
    project_id, drawing_id, category, content,
    value_json, location_json, extractor, confidence,
    source_kind, is_active, supersedes, reviewed_by, reviewed_at, extraction_version
)
VALUES (
    :project_id, :drawing_id, :category, :content,
    CAST(:value_json AS jsonb), CAST(:location_json AS jsonb), :extractor, :confidence,
    :source_kind, :is_active, :supersedes, :reviewed_by, now(), 1
)
"""


async def persist_verify(db: Any, params: dict) -> None:
    """落库人审修正:置原 auto 失活(若有)+ 插入 verified 行。"""
    if params.get("deactivate_id"):
        await db.execute(_DEACTIVATE_SQL, {"id": params["deactivate_id"]})
    await db.execute(_INSERT_VERIFIED_SQL, params["insert"])


# ── 档案读取 ─────────────────────────────────────────────────────

_FETCH_DRAWING_ROWS_SQL = """
SELECT id, drawing_id, category, content, value_json, location_json,
       extractor, confidence, source_kind, is_active
FROM drawing_extracted_info
WHERE drawing_id = :drawing_id
"""

_FETCH_PROJECT_CATEGORY_SQL = """
SELECT dei.id, dei.drawing_id, dei.category, dei.content, dei.value_json,
       dei.location_json, dei.extractor, dei.confidence,
       dei.source_kind, dei.is_active,
       d.drawing_no, d.title AS drawing_title, d.discipline
FROM drawing_extracted_info dei
JOIN drawings d ON d.id = dei.drawing_id
WHERE dei.project_id = :project_id AND dei.category = :category
"""


def _coerce_row(row: Any) -> dict:
    d = dict(row)
    v = d.get("value_json")
    if isinstance(v, str):
        try:
            d["value_json"] = json.loads(v)
        except (ValueError, TypeError):
            pass
    loc = d.get("location_json")
    if isinstance(loc, str):
        try:
            d["location_json"] = json.loads(loc)
        except (ValueError, TypeError):
            pass
    if d.get("confidence") is not None:
        d["confidence"] = float(d["confidence"])
    return d


async def fetch_drawing_archive(db: Any, drawing_id: str) -> list[dict]:
    """单图档案生效值(按 category 择优)。"""
    rows = [_coerce_row(r) for r in
            await db.fetch_all(_FETCH_DRAWING_ROWS_SQL, {"drawing_id": drawing_id})]
    return effective_values(rows)


async def fetch_project_category(db: Any, project_id: str, category: str) -> list[dict]:
    """全项目某类别生效值(建模消费:elevation / axis)。"""
    rows = [_coerce_row(r) for r in await db.fetch_all(
        _FETCH_PROJECT_CATEGORY_SQL, {"project_id": project_id, "category": category}
    )]
    return effective_values(rows)
