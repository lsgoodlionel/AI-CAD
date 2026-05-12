"""
知识图谱引擎 + 经济测算引擎的所有业务参数，均在此 API 管理。
参数以 key-value JSONB 存储，前端按参数 schema 渲染对应的表单控件。
变更后即时生效（引擎每次调用前从数据库/缓存读取参数）。
"""
from uuid import UUID
from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import get_db, require_admin

router = APIRouter(prefix="/admin/engine-params", tags=["admin-engine"])

EngineScope = Literal["kg", "economic", "ai_review", "rebar"]


# ── 参数 schema（描述每个参数的前端渲染方式）──────────────────────

KG_PARAM_SCHEMA: list[dict] = [
    # NLP 提取
    {"key": "classify_batch_size",       "label": "条文分类批次大小",    "type": "number", "default": 20,   "unit": "条/批"},
    {"key": "extract_confidence_min",    "label": "实体提取最低置信度",   "type": "slider", "default": 0.75, "min": 0.5, "max": 1.0, "step": 0.05},
    {"key": "mandatory_obligation_words","label": "强条义务词（逗号分隔）","type": "tags",   "default": "应,必须,严禁,不得,不应"},
    {"key": "recommended_obligation_words","label":"推荐义务词",           "type": "tags",   "default": "宜,建议"},
    # 图谱查询
    {"key": "graph_query_depth_max",     "label": "交叉引用最大追溯深度", "type": "number", "default": 3,    "unit": "层"},
    {"key": "applicable_building_scope", "label": "默认检查建筑类别",     "type": "multiselect",
     "options": ["住宅","高层公共建筑","工业建筑","地下建筑","仓储建筑"], "default": ["住宅","高层公共建筑"]},
    # 合规推理
    {"key": "kg_confidence_confirmed",   "label": "CONFIRMED 置信度阈值", "type": "slider", "default": 0.90, "min": 0.7, "max": 1.0, "step": 0.01},
    {"key": "kg_confidence_probable",    "label": "PROBABLE 置信度阈值",  "type": "slider", "default": 0.75, "min": 0.5, "max": 0.9, "step": 0.01},
    {"key": "kg_confidence_discard",     "label": "丢弃阈值（低于此不输出）","type": "slider", "default": 0.60, "min": 0.3, "max": 0.8, "step": 0.01},
    {"key": "max_issues_per_drawing",    "label": "单张图纸最大输出问题数","type": "number", "default": 100},
    # 规范库
    {"key": "regulation_recheck_days",   "label": "规范更新后强制复查天数","type": "number", "default": 30,   "unit": "天"},
    {"key": "embedding_model",           "label": "向量化模型",           "type": "select",
     "options": ["BAAI/bge-m3","BAAI/bge-large-zh","text-embedding-3-small","text-embedding-3-large"],
     "default": "BAAI/bge-m3"},
    {"key": "reranker_model",            "label": "重排序模型",           "type": "select",
     "options": ["BAAI/bge-reranker-v2-m3","BAAI/bge-reranker-large","none"],
     "default": "BAAI/bge-reranker-v2-m3"},
    {"key": "rag_top_k",                 "label": "RAG 检索条数（重排前）","type": "number", "default": 10},
    {"key": "rag_top_k_after_rerank",    "label": "RAG 重排后保留条数",   "type": "number", "default": 3},
]

ECONOMIC_PARAM_SCHEMA: list[dict] = [
    # 钢筋翻样
    {"key": "standard_bar_lengths",      "label": "标准钢筋定尺长度(mm)", "type": "tags",   "default": "9000,12000"},
    {"key": "seismic_factor_grade1",     "label": "一级抗震锚固系数 ζaE", "type": "number", "default": 1.15, "step": 0.01},
    {"key": "seismic_factor_grade2",     "label": "二级抗震锚固系数 ζaE", "type": "number", "default": 1.15, "step": 0.01},
    {"key": "seismic_factor_grade3",     "label": "三级抗震锚固系数 ζaE", "type": "number", "default": 1.05, "step": 0.01},
    {"key": "seismic_factor_grade4",     "label": "四级抗震锚固系数 ζaE", "type": "number", "default": 1.00, "step": 0.01},
    {"key": "lap_factor_25pct",          "label": "搭接率≤25%时搭接系数", "type": "number", "default": 1.20, "step": 0.01},
    {"key": "lap_factor_50pct",          "label": "搭接率≤50%时搭接系数", "type": "number", "default": 1.40, "step": 0.01},
    {"key": "lap_factor_100pct",         "label": "搭接率100%时搭接系数", "type": "number", "default": 1.60, "step": 0.01},
    # 现场损耗率（粗放管理基准，用于计算节约潜力）
    {"key": "field_waste_rate_d6_10",    "label": "Φ6-10 现场粗放损耗率","type": "slider", "default": 0.06, "min": 0.01, "max": 0.15, "step": 0.01},
    {"key": "field_waste_rate_d12_16",   "label": "Φ12-16 现场粗放损耗率","type": "slider", "default": 0.045,"min": 0.01, "max": 0.15, "step": 0.01},
    {"key": "field_waste_rate_d18_22",   "label": "Φ18-22 现场粗放损耗率","type": "slider", "default": 0.04, "min": 0.01, "max": 0.15, "step": 0.01},
    {"key": "field_waste_rate_d25_plus", "label": "Φ25+ 现场粗放损耗率", "type": "slider", "default": 0.035,"min": 0.01, "max": 0.15, "step": 0.01},
    # 优化目标损耗率
    {"key": "target_waste_rate",         "label": "集中翻样目标损耗率",   "type": "slider", "default": 0.015,"min": 0.005,"max": 0.05,"step": 0.005},
    # 优化触发阈值
    {"key": "utilization_downgrade_threshold","label": "钢筋降级触发承载力富余率","type":"slider","default":0.25,"min":0.1,"max":0.5,"step":0.05},
    {"key": "masonry_cut_ratio_threshold","label": "砌体切砖率触发排版优化阈值","type":"slider","default":0.15,"min":0.05,"max":0.30,"step":0.05},
    # 创效提案自动推送
    {"key": "auto_proposal_min_saving",  "label": "AI 自动推送创效线索最低节约额(元)","type":"number","default":5000},
    # 价格库
    {"key": "price_db_update_reminder_days","label": "单价库更新提醒周期","type":"number","default":30,"unit":"天"},
    # 混凝土/模板
    {"key": "concrete_overlap_deduct",   "label": "梁柱相交区混凝土扣除规则","type":"select",
     "options": ["按柱截面扣除","不扣除（保守）"],"default":"按柱截面扣除"},
    {"key": "formwork_beam_side_deduct", "label": "梁侧模板扣除楼板厚度","type":"select",
     "options": ["扣除","不扣除"],"default":"扣除"},
]


# ── API ────────────────────────────────────────────────────────────

class ParamUpdate(BaseModel):
    param_key: str
    param_value: Any
    description: str | None = None


@router.get("/schema/{scope}")
async def get_param_schema(scope: EngineScope, _=Depends(require_admin)):
    """返回参数 schema，供前端动态渲染表单"""
    match scope:
        case "kg":       return KG_PARAM_SCHEMA
        case "economic": return ECONOMIC_PARAM_SCHEMA
        case _:          raise HTTPException(404, f"未知 scope: {scope}")


@router.get("/{scope}")
async def get_params(scope: EngineScope, db=Depends(get_db), _=Depends(require_admin)):
    """返回当前所有参数值（数据库存储 + schema 默认值合并）"""
    schema = KG_PARAM_SCHEMA if scope == "kg" else ECONOMIC_PARAM_SCHEMA
    rows = await db.fetch_all(
        "SELECT param_key, param_value, description, updated_at, updated_by "
        "FROM engine_params WHERE scope=$1", scope,
    )
    stored = {r["param_key"]: dict(r) for r in rows}
    result = []
    for item in schema:
        key = item["key"]
        if key in stored:
            result.append({**item, "value": stored[key]["param_value"],
                           "updated_at": stored[key]["updated_at"],
                           "updated_by": stored[key]["updated_by"]})
        else:
            result.append({**item, "value": item["default"], "updated_at": None})
    return result


@router.put("/{scope}/{param_key}")
async def update_param(
    scope: EngineScope, param_key: str, body: ParamUpdate,
    db=Depends(get_db), _=Depends(require_admin),
):
    await db.execute(
        """INSERT INTO engine_params (scope, param_key, param_value, description, updated_at)
           VALUES ($1,$2,$3,$4,now())
           ON CONFLICT (scope, param_key) DO UPDATE
           SET param_value=$3, description=$4, updated_at=now()""",
        scope, param_key, body.param_value, body.description,
    )
    return {"ok": True}


@router.post("/{scope}/reset/{param_key}")
async def reset_param(
    scope: EngineScope, param_key: str,
    db=Depends(get_db), _=Depends(require_admin),
):
    """重置为 schema 默认值"""
    await db.execute(
        "DELETE FROM engine_params WHERE scope=$1 AND param_key=$2", scope, param_key,
    )
    return {"ok": True}


@router.get("/{scope}/value/{param_key}")
async def get_single_param(
    scope: EngineScope, param_key: str, db=Depends(get_db),
):
    """引擎内部调用：获取单个参数值（带默认值兜底）"""
    schema = KG_PARAM_SCHEMA if scope == "kg" else ECONOMIC_PARAM_SCHEMA
    schema_item = next((s for s in schema if s["key"] == param_key), None)
    row = await db.fetch_one(
        "SELECT param_value FROM engine_params WHERE scope=$1 AND param_key=$2",
        scope, param_key,
    )
    if row:
        return {"value": row["param_value"]}
    if schema_item:
        return {"value": schema_item["default"]}
    raise HTTPException(404, f"参数 {param_key} 不存在")
