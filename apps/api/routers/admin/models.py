"""LLM 模型 CRUD — 管理后台 API"""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.llm.router import ModelRouter
from dependencies import get_db, get_router, require_admin

router = APIRouter(prefix="/admin/llm/models", tags=["admin-llm"])


class ModelCreate(BaseModel):
    provider_id: UUID
    model_id: str               # "claude-sonnet-4-6"
    display_name: str
    context_window: int | None = None
    supports_vision: bool = False
    input_price_per_1m: float = 0.0
    output_price_per_1m: float = 0.0
    benchmark_score: float | None = None


class ModelUpdate(BaseModel):
    display_name: str | None = None
    context_window: int | None = None
    supports_vision: bool | None = None
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None
    benchmark_score: float | None = None
    is_active: bool | None = None


@router.get("")
async def list_models(
    provider_id: UUID | None = None,
    db=Depends(get_db),
    _=Depends(require_admin),
):
    where = "WHERE lm.provider_id=$1" if provider_id else ""
    args = [provider_id] if provider_id else []
    rows = await db.fetch_all(
        f"""
        SELECT lm.id, lm.model_id, lm.display_name, lm.context_window,
               lm.supports_vision, lm.input_price_per_1m, lm.output_price_per_1m,
               lm.benchmark_score, lm.is_active, lm.created_at,
               lp.id AS provider_id, lp.name AS provider_name,
               lp.provider_type
        FROM llm_models lm
        JOIN llm_providers lp ON lm.provider_id = lp.id
        {where}
        ORDER BY lp.name, lm.display_name
        """,
        *args,
    )
    return [dict(r) for r in rows]


@router.post("", status_code=201)
async def create_model(
    body: ModelCreate,
    db=Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
    _=Depends(require_admin),
):
    row = await db.fetch_one(
        """
        INSERT INTO llm_models
            (provider_id, model_id, display_name, context_window,
             supports_vision, input_price_per_1m, output_price_per_1m, benchmark_score)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id
        """,
        body.provider_id, body.model_id, body.display_name,
        body.context_window, body.supports_vision,
        body.input_price_per_1m, body.output_price_per_1m,
        body.benchmark_score,
    )
    model_router.invalidate_cache()
    return {"id": str(row["id"])}


@router.patch("/{model_db_id}")
async def update_model(
    model_db_id: UUID,
    body: ModelUpdate,
    db=Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
    _=Depends(require_admin),
):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "无更新字段")
    sets = ", ".join(f"{k}=${i+2}" for i, k in enumerate(fields))
    await db.execute(
        f"UPDATE llm_models SET {sets} WHERE id=$1",
        model_db_id, *fields.values(),
    )
    model_router.invalidate_cache()
    return {"ok": True}


@router.delete("/{model_db_id}")
async def delete_model(
    model_db_id: UUID,
    db=Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
    _=Depends(require_admin),
):
    in_use = await db.fetch_one(
        "SELECT id FROM engine_model_configs WHERE model_id=$1 LIMIT 1",
        model_db_id,
    )
    if in_use:
        raise HTTPException(
            400,
            "该模型正被引擎配置使用，请先解除引擎绑定再删除",
        )
    await db.execute("DELETE FROM llm_models WHERE id=$1", model_db_id)
    model_router.invalidate_cache()
    return {"ok": True}
