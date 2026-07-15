"""D-18 评测金标准自举：从 `model_review_actions`（migrations/024_review_actions.sql）
人审动作埋点回流合规问题金标签（对齐 docs/PHASE_D_GRAPHRAG.md §3.6/§5）。

D-18 尚无专门标注项目，冷启动来源复用 Phase C 泳道D 已建立的人审动作埋点体系：
审校人员对 GraphRAG 灰度输出（或现有 KG+RAG 问题）做 confirm(确认)/reject(否定)/
reclass(改判) 时，这些动作本身就是可复用的金标签来源，与 C-16/C-17 的「数据飞轮」
思路一致——不必等专门标注项目单独立项，复用既有审校流程滚雪球积累评测集。

## `target_kind='compliance'` 行的字段编码约定（本轮定义）

`model_review_actions` 是跨用途通用表（symbol/element/topology/naming/compliance
共用），`compliance` 用途此前只有队列生成端（`routers/model_review.py::
_explicit_items`，读 `project_models.scene['review_candidates']`）读取，尚无生产者
写入过 compliance 动作。本函数按以下约定解析（供未来把 D-18 合规问题接入审校
队列时遵循）：

- `target_id`：期望编码为归一化前的 `regulation_ref`（`_explicit_items` 把
  `review_candidates` 条目的 `id`/`target_id` 原样透传到该列，接入方须以
  `AIIssue.regulation_ref` 作为 `target_id`）。
- `discipline`：直接对应 `ComplianceGt.discipline`。
- `note`：可选 JSON，形如 `{"severity": "major", "obligation_level": "MUST",
  "snippet": "..."}`；缺失字段各自回退默认值（`major` / `SHOULD` / 空文本）。
- 同一 `(project_id, drawing_id, target_id)` 的多条动作按 `created_at` 取
  **最新**状态：最新动作是 `reject` → 人工否定，不计入金标准正例；最新动作是
  `confirm`/`reclass` → 计入金标准。

## 已知局限（诚实边界，对齐项目 Phase C M1 式的表述）

在「把 D-18 合规问题接入 `review_candidates` 队列」这一步完成之前，DB 里大概率
没有 `target_kind='compliance'` 的真实数据——`bootstrap_gold_from_review_actions`
在这种情况下返回空列表，这不是 bug，是如实反映当前数据现状；上层评测应把「金标准
暂缺」视为合法状态（harness 已就绪，等真实标注滚雪球）。
"""
from __future__ import annotations

import json
import logging

from .metrics import ComplianceGt, normalize_regulation_ref

logger = logging.getLogger(__name__)

_DEFAULT_SEVERITY = "major"
_DEFAULT_OBLIGATION = "SHOULD"
_VALID_SEVERITIES = {"critical", "major", "minor", "info"}
_VALID_OBLIGATIONS = {"MUST", "SHOULD", "MAY", "MUST_NOT"}

_SELECT_SQL = """
SELECT project_id, drawing_id, target_id, action_type, discipline, note, created_at
FROM model_review_actions
WHERE target_kind = 'compliance'
ORDER BY project_id, drawing_id, target_id, created_at ASC
"""


def _parse_note(note: str | None) -> dict:
    if not note:
        return {}
    try:
        data = json.loads(note)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _row_to_gold(latest: dict) -> ComplianceGt | None:
    """把一个 `(project_id, drawing_id, target_id)` 分组的最新动作行转成
    `ComplianceGt`；最新动作是 `reject` 时返回 None（不计入金标准正例）。
    """
    if latest.get("action_type") == "reject":
        return None

    note = _parse_note(latest.get("note"))
    severity = str(note.get("severity") or _DEFAULT_SEVERITY).lower()
    if severity not in _VALID_SEVERITIES:
        severity = _DEFAULT_SEVERITY
    obligation = str(note.get("obligation_level") or _DEFAULT_OBLIGATION).upper()
    if obligation not in _VALID_OBLIGATIONS:
        obligation = _DEFAULT_OBLIGATION

    return ComplianceGt(
        drawing_id=str(latest.get("drawing_id") or ""),
        regulation_ref=normalize_regulation_ref(str(latest.get("target_id") or "")),
        discipline=str(latest.get("discipline") or ""),
        obligation_level=obligation,
        severity=severity,
        snippet=str(note.get("snippet") or ""),
    )


def rows_to_gold(rows: list[dict]) -> list[ComplianceGt]:
    """按 `(project_id, drawing_id, target_id)` 分组、取最新动作决定金标准去留。

    纯函数：输入需已按 `created_at` 升序排好（`_SELECT_SQL` 已保证），输出
    确定性（同输入同输出），便于离线单测不依赖真实数据库。
    """
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        target_id = str(row.get("target_id") or "")
        if not target_id:
            continue  # 缺 target_id 无法回溯 regulation_ref，跳过（诚实丢弃而非臆造）
        key = (str(row.get("project_id") or ""), str(row.get("drawing_id") or ""), target_id)
        groups.setdefault(key, []).append(row)

    gold: list[ComplianceGt] = []
    for actions in groups.values():
        latest = actions[-1]  # 已按 created_at 升序，取最后一条即最新状态
        gt = _row_to_gold(latest)
        if gt is not None:
            gold.append(gt)
    return gold


async def bootstrap_gold_from_review_actions(db) -> list[ComplianceGt]:
    """从 `model_review_actions`（`target_kind='compliance'`）自举 D-18 评测金标准。

    只读查询，不写库。查询失败（如表不存在于本地未迁移环境）时优雅降级为空
    列表，不级联崩溃调用方——同项目「优雅降级」惯例（`fusion.py` 同款风格）。
    """
    try:
        rows = await db.fetch_all(_SELECT_SQL)
    except Exception as e:  # noqa: BLE001 - 评测工具链只读辅助函数，不应向上抛出崩溃评测流程
        logger.warning("[GraphRAG eval] 自举金标准查询失败，回退空列表: %s", e)
        return []
    return rows_to_gold([dict(r) for r in rows])
