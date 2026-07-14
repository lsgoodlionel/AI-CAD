"""CAD 平台 FastAPI 应用入口"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.database import connect, disconnect

# ── 路由模块 ──────────────────────────────────────────────────
from routers.auth import router as auth_router
from routers.admin.providers import router as providers_router
from routers.admin.models import router as models_router
from routers.admin.engine_configs import router as engine_configs_router
from routers.admin.engine_params import router as engine_params_router
from routers.admin.call_logs import router as call_logs_router
from routers.admin.users import router as admin_users_router
from routers.projects import router as projects_router
from routers.project_models import router as project_models_router
from routers.model_spotting import router as model_spotting_router
from routers.model_annotations import router as model_annotations_router
from routers.model_review import router as model_review_router
from routers.findings import router as findings_router
from routers.pipeline import router as pipeline_router
from routers.drawings import router as drawings_router
from routers.review_batches import router as review_batches_router
from routers.technical_review import router as technical_review_router
from routers.economic_review import router as economic_review_router
from routers.settlement_review import router as settlement_review_router
from routers.incentive import router as incentive_router
from routers.regulations import router as regulations_router
from routers.economic_calc import router as economic_calc_router
from routers.dashboard import router as dashboard_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    await connect()
    yield
    await disconnect()


app = FastAPI(
    title="CAD 图纸深化全过程管理平台",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由注册 ──────────────────────────────────────────────────

API_V1 = "/api/v1"

app.include_router(auth_router,           prefix=API_V1)
app.include_router(providers_router,      prefix=API_V1)
app.include_router(models_router,         prefix=API_V1)
app.include_router(engine_configs_router, prefix=API_V1)
app.include_router(engine_params_router,  prefix=API_V1)
app.include_router(call_logs_router,      prefix=API_V1)
app.include_router(admin_users_router,    prefix=API_V1)
app.include_router(projects_router,       prefix=API_V1)
app.include_router(project_models_router, prefix=API_V1)
app.include_router(model_spotting_router, prefix=API_V1)
app.include_router(model_annotations_router, prefix=API_V1)
app.include_router(model_review_router,   prefix=API_V1)
app.include_router(findings_router,       prefix=API_V1)
app.include_router(pipeline_router,       prefix=API_V1)
app.include_router(drawings_router,       prefix=API_V1)
app.include_router(review_batches_router, prefix=API_V1)
app.include_router(technical_review_router, prefix=API_V1)
app.include_router(economic_review_router,  prefix=API_V1)
app.include_router(settlement_review_router, prefix=API_V1)
app.include_router(incentive_router,        prefix=API_V1)
app.include_router(regulations_router,      prefix=API_V1)
app.include_router(economic_calc_router,    prefix=API_V1)
app.include_router(dashboard_router,        prefix=API_V1)


@app.get("/health")
async def health():
    return {"status": "ok"}
