"""Pipeline 编排层开关与阈值读取（D-08）。

原则：**缺省开**——找不到配置就当启用，找不到阈值就退回到有据可查的默认值
（优先复用既有引擎参数，如经济测算引擎已有的 ``auto_proposal_min_saving`` /
``steel_price_per_ton``，不重复造轮子）。

开关存储：复用既有 ``engine_params`` 表（``scope='pipeline'``），
``param_key`` 支持两种粒度：
- 全局键：例如 ``ai_review_to_rebuild_suggestion_enabled``
- 项目级覆盖键：``{project_id}:ai_review_to_rebuild_suggestion_enabled``
  （存在项目级键时优先于全局键）

本模块不新建管理页/schema（不在本工作块文件边界内），管理后台可后续在
``routers/admin/engine_params.py`` 补充 ``scope='pipeline'`` 的 schema 项；
在此之前，缺省值走本文件内的具名常量。
"""
from __future__ import annotations

import json
from typing import Any

# ── 管线步骤开关键名 ─────────────────────────────────────────────
STEP_AI_REVIEW_TO_REBUILD_SUGGESTION = "ai_review_to_rebuild_suggestion_enabled"
STEP_MODEL_BUILT_TO_PROPOSAL_SUGGESTION = "model_built_to_proposal_suggestion_enabled"

# ── 默认阈值（无 engine_params 配置时生效） ───────────────────────
# 自上次建模以来，达到多少张「审图完成」的图纸即认为模型可能已过期。
DEFAULT_REBUILD_IMPACT_MIN_DRAWINGS = 1
# 钢筋价（元/吨），用于把「模型重建后钢筋量下降」换算为预估节约额；
# 与 routers/economic_calc.py 的 steel_price_per_ton 默认值保持一致。
DEFAULT_STEEL_PRICE_PER_TON = 4500.0
# 创效建议最低节约额（元）；与 admin/engine_params.py ECONOMIC_PARAM_SCHEMA
# 中 auto_proposal_min_saving 的默认值保持一致（CLAUDE.md 经济测算参数表）。
DEFAULT_QTO_SAVING_MIN_YUAN = 5000.0

_PIPELINE_SCOPE = "pipeline"
_ECONOMIC_SCOPE = "economic"


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
    return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _fetch_param(db, scope: str, key: str) -> Any:
    """读取单个 engine_params 值；缺省表/记录不存在时返回 None（缺省开语义由调用方兜底）。"""
    try:
        row = await db.fetch_one(
            "SELECT param_value FROM engine_params WHERE scope=:scope AND param_key=:key",
            {"scope": scope, "key": key},
        )
    except Exception:
        # engine_params 表结构演进/连接异常时不阻断管线主流程——按缺省开/默认阈值处理。
        return None
    if row is None:
        return None
    value = row["param_value"] if "param_value" in row else None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


async def is_step_enabled(db, project_id: str, step_key: str) -> bool:
    """项目级开关优先于全局开关；两者皆缺省时返回 True（自动打底默认开）。"""
    project_value = await _fetch_param(db, _PIPELINE_SCOPE, f"{project_id}:{step_key}")
    if project_value is not None:
        return _coerce_bool(project_value, True)
    global_value = await _fetch_param(db, _PIPELINE_SCOPE, step_key)
    return _coerce_bool(global_value, True)


async def get_rebuild_impact_min_drawings(db) -> int:
    value = await _fetch_param(db, _PIPELINE_SCOPE, "rebuild_impact_min_drawings")
    return _coerce_int(value, DEFAULT_REBUILD_IMPACT_MIN_DRAWINGS)


async def get_steel_price_per_ton(db) -> float:
    value = await _fetch_param(db, _ECONOMIC_SCOPE, "steel_price_per_ton")
    return _coerce_float(value, DEFAULT_STEEL_PRICE_PER_TON)


async def get_qto_saving_threshold_yuan(db) -> float:
    value = await _fetch_param(db, _ECONOMIC_SCOPE, "auto_proposal_min_saving")
    return _coerce_float(value, DEFAULT_QTO_SAVING_MIN_YUAN)
