"""已采纳的学习规则:让建议**真正改变系统行为**,而不是只躺在列表里。

被识别路径直接读取:
- `vocabulary`      → 专业词表扩充(`title_block_discipline.normalize_discipline`)
- `ocr_correction`  → OCR 糊字还原(区域重识别文本先过一遍)
- `threshold`       → 参数覆盖(候选轴线最小跨度等)

**为什么要缓存**:识别路径是热路径,每张图查库会明显拖慢。用带 TTL 的进程内缓存,
采纳新规则后主动失效,保证「采纳即生效」不是空话。
"""
from __future__ import annotations

import time
from typing import Any

#: 规则缓存 TTL(秒)。识别是热路径,不能每张图都查库。
CACHE_TTL_SECONDS = 60

_cache: dict[str, tuple[float, dict]] = {}

_FETCH_SQL = """
SELECT rule_type, rule_key, rule_value
FROM learned_rules
WHERE project_id IS NULL OR project_id = CAST(:project_id AS uuid)
"""

_UPSERT_SQL = """
INSERT INTO learned_rules
    (project_id, rule_type, rule_key, rule_value, source_suggestion_id)
VALUES (CAST(:project_id AS uuid), :rule_type, :rule_key, :rule_value,
        CAST(:suggestion_id AS uuid))
ON CONFLICT (project_id, rule_type, rule_key) DO UPDATE SET
    rule_value = EXCLUDED.rule_value,
    source_suggestion_id = EXCLUDED.source_suggestion_id
RETURNING id
"""


def invalidate_cache(project_id: str | None = None) -> None:
    """采纳新规则后调用——不清缓存,「采纳即生效」就是空话。"""
    if project_id is None:
        _cache.clear()
    else:
        _cache.pop(project_id, None)


async def fetch_rules(db: Any, project_id: str) -> dict[str, dict[str, str]]:
    """取该项目可用规则(含全局),按类型分组。带 TTL 缓存。"""
    cached = _cache.get(project_id)
    now = time.monotonic()
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    grouped: dict[str, dict[str, str]] = {}
    try:
        rows = await db.fetch_all(_FETCH_SQL, {"project_id": project_id})
    except Exception:  # noqa: BLE001 — 学习规则读不到就按内置行为跑
        return {}
    for r in rows:
        grouped.setdefault(r["rule_type"], {})[r["rule_key"]] = r["rule_value"]
    _cache[project_id] = (now, grouped)
    return grouped


async def save_rule(
    db: Any, *, project_id: str | None, rule_type: str, rule_key: str,
    rule_value: str, suggestion_id: str | None,
) -> str | None:
    """写入一条已采纳的规则并让缓存失效。"""
    row = await db.fetch_one(_UPSERT_SQL, {
        "project_id": project_id, "rule_type": rule_type,
        "rule_key": rule_key, "rule_value": rule_value,
        "suggestion_id": suggestion_id})
    invalidate_cache(project_id)
    return str(row["id"]) if row is not None else None


def apply_ocr_corrections(text: str, corrections: dict[str, str]) -> str:
    """把学到的糊字映射应用到识别原文。

    **按原文长度降序替换**:短串可能是长串的子串(如「建 个」之于「建 个人」),
    先replace 短的会把长的拆坏。
    """
    if not text or not corrections:
        return text
    out = text
    for raw in sorted(corrections, key=len, reverse=True):
        if raw and raw in out:
            out = out.replace(raw, corrections[raw])
    return out


def threshold_of(
    rules: dict[str, dict[str, str]], key: str, default: float,
) -> float:
    """取学到的阈值覆盖;没有或值非法则用内置默认(不让坏规则毁掉识别)。"""
    raw = (rules.get("threshold") or {}).get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def extra_vocabulary(rules: dict[str, dict[str, str]]) -> dict[str, str]:
    """学到的词表扩充 {图框写法: 规范专业名}。"""
    return dict(rules.get("vocabulary") or {})
