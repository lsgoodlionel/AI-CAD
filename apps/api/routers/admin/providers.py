"""提供商 CRUD — 管理后台 API"""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.llm.router import ModelRouter
from core.llm.providers.ollama_provider import OllamaProvider
from dependencies import get_db, get_router, require_admin

router = APIRouter(prefix="/admin/llm/providers", tags=["admin-llm"])


class ProviderCreate(BaseModel):
    name: str
    provider_type: str           # anthropic | openai_compat | ollama | custom_http
    base_url: str | None = None
    api_key_env: str | None = None   # 环境变量名，不存明文
    timeout_sec: int = 120
    metadata: dict = {}


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_sec: int | None = None
    is_active: bool | None = None
    metadata: dict | None = None


@router.get("")
async def list_providers(db=Depends(get_db), _=Depends(require_admin)):
    rows = await db.fetch_all(
        "SELECT id, name, provider_type, base_url, api_key_env, timeout_sec, is_active, metadata "
        "FROM llm_providers ORDER BY name"
    )
    return [dict(r) for r in rows]


@router.post("", status_code=201)
async def create_provider(body: ProviderCreate, db=Depends(get_db), _=Depends(require_admin)):
    row = await db.fetch_one(
        """INSERT INTO llm_providers (name, provider_type, base_url, api_key_env, timeout_sec, metadata)
           VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
        body.name, body.provider_type, body.base_url,
        body.api_key_env, body.timeout_sec, body.metadata,
    )
    return {"id": str(row["id"])}


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: UUID, body: ProviderUpdate,
    db=Depends(get_db), model_router: ModelRouter = Depends(get_router),
    _=Depends(require_admin),
):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "无更新字段")
    sets = ", ".join(f"{k}=${i+2}" for i, k in enumerate(fields))
    await db.execute(
        f"UPDATE llm_providers SET {sets} WHERE id=$1",
        provider_id, *fields.values(),
    )
    model_router.invalidate_cache()
    return {"ok": True}


@router.delete("/{provider_id}")
async def delete_provider(
    provider_id: UUID, db=Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
    _=Depends(require_admin),
):
    await db.execute("DELETE FROM llm_providers WHERE id=$1", provider_id)
    model_router.invalidate_cache()
    return {"ok": True}


@router.get("/health-all")
async def check_all_provider_health(
    db=Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
    _=Depends(require_admin),
):
    rows = await db.fetch_all(
        "SELECT name, provider_type, base_url, api_key_env FROM llm_providers WHERE is_active=true ORDER BY name"
    )
    result = {}
    for row in rows:
        provider = dict(row)
        result[provider["name"]] = await model_router._check_provider_health(provider)
    return result


@router.get("/{provider_id}/available-models")
async def list_provider_available_models(
    provider_id: UUID,
    db=Depends(get_db),
    _=Depends(require_admin),
):
    row = await db.fetch_one(
        "SELECT name, provider_type, base_url FROM llm_providers WHERE id=$1",
        provider_id,
    )
    if not row:
        raise HTTPException(404, "提供商不存在")
    provider = dict(row)
    if provider["provider_type"] != "ollama":
        return {"provider": provider["name"], "models": []}

    models = await OllamaProvider(
        base_url=provider.get("base_url") or "http://host.docker.internal:11434"
    ).list_models()
    return {
        "provider": provider["name"],
        "models": [
            {
                "model_id": m.get("model") or m.get("name"),
                "name": m.get("name") or m.get("model"),
                "size": m.get("size"),
                "modified_at": m.get("modified_at"),
                "details": m.get("details") or {},
            }
            for m in models
            if m.get("model") or m.get("name")
        ],
    }


@router.post("/{provider_id}/health-check")
async def check_provider_health(
    provider_id: UUID, db=Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
    _=Depends(require_admin),
):
    row = await db.fetch_one(
        "SELECT name, provider_type, base_url, api_key_env FROM llm_providers WHERE id=$1",
        provider_id,
    )
    if not row:
        raise HTTPException(404)
    healthy = await model_router._check_provider_health(dict(row))
    return {"healthy": healthy}
