"""
引擎模型配置 CRUD。
每个引擎（kg_reasoning/rag_qa/rebar_extraction/...）可独立配置：
  主模型 / 备用链 / 批量模型 及其推理参数。
变更后立即失效路由缓存，30s 内所有 Worker 生效。
"""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.llm.router import ModelRouter
from dependencies import get_db, get_router, require_admin

router = APIRouter(prefix="/admin/llm/engine-configs", tags=["admin-llm"])

# 所有引擎名称（供前端下拉选择）
ENGINE_NAMES = [
    # 知识图谱引擎
    "regulation_classifier",     # 条文分类（Haiku）
    "regulation_extractor",      # 条文实体提取（Sonnet）
    "kg_compliance_reasoning",   # 合规推理（Sonnet）
    "kg_suggestion_generator",   # 修改建议生成（Haiku）
    "kg_diff_analyzer",          # 规范版本对比（Sonnet）
    # RAG 引擎
    "rag_qa",                    # 规范问答（Sonnet）
    "rag_rewriter",              # 查询改写（Haiku）
    # 经济测算引擎
    "rebar_annotation_parser",   # 钢筋标注解析（Haiku）
    "cost_explanation_writer",   # 经济说明生成（Haiku）
    "optimization_hint_writer",  # 优化建议生成（Haiku）
    # 审查报告引擎
    "report_summary_writer",     # 审查摘要（Sonnet）
    # 视觉引擎（支持多模态模型）
    "drawing_visual_analyzer",   # 复杂图纸视觉理解（Sonnet Vision）
    # 创效激励引擎
    "incentive_description_writer",  # 提案描述生成（Haiku）
]

TASK_TYPES = ["primary", "fallback_1", "fallback_2", "batch"]


class EngineConfigCreate(BaseModel):
    engine_name: str
    task_type: str = "primary"
    model_id: UUID                          # llm_models.id
    temperature: float = Field(0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(2048, ge=1, le=32000)
    top_p: float = Field(1.0, ge=0.0, le=1.0)
    frequency_penalty: float = Field(0.0, ge=-2.0, le=2.0)
    prompt_template_version: str | None = None
    extra_params: dict | None = None


class EngineConfigUpdate(BaseModel):
    model_id: UUID | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    prompt_template_version: str | None = None
    extra_params: dict | None = None
    is_enabled: bool | None = None


@router.get("/engines")
async def list_engine_names(_=Depends(require_admin)):
    return ENGINE_NAMES


@router.get("")
async def list_engine_configs(
    engine_name: str | None = None,
    db=Depends(get_db), _=Depends(require_admin),
):
    where = "WHERE emc.engine_name=$1" if engine_name else ""
    args = [engine_name] if engine_name else []
    rows = await db.fetch_all(
        f"""
        SELECT emc.id, emc.engine_name, emc.task_type, emc.is_enabled,
               emc.temperature, emc.max_tokens, emc.top_p, emc.frequency_penalty,
               emc.prompt_template_version, emc.extra_params,
               lm.id AS model_db_id, lm.model_id, lm.display_name,
               lp.name AS provider_name, lp.provider_type,
               lm.input_price_per_1m, lm.output_price_per_1m
        FROM engine_model_configs emc
        JOIN llm_models lm ON emc.model_id = lm.id
        JOIN llm_providers lp ON lm.provider_id = lp.id
        {where}
        ORDER BY emc.engine_name, emc.task_type
        """,
        *args,
    )
    return [dict(r) for r in rows]


@router.post("", status_code=201)
async def create_engine_config(
    body: EngineConfigCreate,
    db=Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
    _=Depends(require_admin),
):
    row = await db.fetch_one(
        """INSERT INTO engine_model_configs
           (engine_name, task_type, model_id, temperature, max_tokens, top_p,
            frequency_penalty, prompt_template_version, extra_params)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
        body.engine_name, body.task_type, body.model_id,
        body.temperature, body.max_tokens, body.top_p,
        body.frequency_penalty, body.prompt_template_version, body.extra_params,
    )
    model_router.invalidate_cache(body.engine_name)
    return {"id": str(row["id"])}


@router.patch("/{config_id}")
async def update_engine_config(
    config_id: UUID, body: EngineConfigUpdate,
    db=Depends(get_db), model_router: ModelRouter = Depends(get_router),
    _=Depends(require_admin),
):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "无更新字段")
    sets = ", ".join(f"{k}=${i+2}" for i, k in enumerate(fields))
    row = await db.fetch_one(
        f"UPDATE engine_model_configs SET {sets}, updated_at=now() WHERE id=$1 RETURNING engine_name",
        config_id, *fields.values(),
    )
    if row:
        model_router.invalidate_cache(row["engine_name"])
    return {"ok": True}


@router.delete("/{config_id}")
async def delete_engine_config(
    config_id: UUID, db=Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
    _=Depends(require_admin),
):
    row = await db.fetch_one(
        "DELETE FROM engine_model_configs WHERE id=$1 RETURNING engine_name", config_id
    )
    if row:
        model_router.invalidate_cache(row["engine_name"])
    return {"ok": True}


@router.get("/summary")
async def engine_config_summary(db=Depends(get_db), _=Depends(require_admin)):
    """管理后台首页：每个引擎当前主模型一览"""
    rows = await db.fetch_all(
        """
        SELECT emc.engine_name, lm.display_name, lm.model_id,
               lp.name AS provider_name, lp.provider_type,
               emc.temperature, emc.max_tokens
        FROM engine_model_configs emc
        JOIN llm_models lm ON emc.model_id = lm.id
        JOIN llm_providers lp ON lm.provider_id = lp.id
        WHERE emc.task_type = 'primary' AND emc.is_enabled = true
        ORDER BY emc.engine_name
        """
    )
    return [dict(r) for r in rows]
