"""
经济测算 API — 钢筋翻样 + 下料优化

POST /drawings/{id}/economic-calc   提交计算（保存至 DB）
GET  /drawings/{id}/economic-calc   读取上次计算结果
"""
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from dependencies import get_db, get_current_user
from core.economic.rebar_calculator import (
    BarItem, calc_anchor_lengths, optimize_cutting,
    FT_TABLE, FY_TABLE,
)
from routers.admin.engine_params import ECONOMIC_PARAM_SCHEMA

router = APIRouter(prefix="/drawings/{drawing_id}/economic-calc", tags=["economic-calc"])


# ── 请求 / 响应 schema ──────────────────────────────────────────

class BarItemReq(BaseModel):
    diameter: int
    steel_grade: str = "HRB400"
    required_length: int   # mm
    count: int = 1

    @field_validator("diameter")
    @classmethod
    def valid_diameter(cls, v: int) -> int:
        allowed = {6, 8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 32}
        if v not in allowed:
            raise ValueError(f"直径必须在 {sorted(allowed)} 中")
        return v

    @field_validator("steel_grade")
    @classmethod
    def valid_grade(cls, v: str) -> str:
        if v not in FY_TABLE:
            raise ValueError(f"钢筋级别必须在 {list(FY_TABLE)} 中")
        return v

    @field_validator("required_length")
    @classmethod
    def positive_length(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("required_length 必须大于 0")
        return v


class CalcRequest(BaseModel):
    concrete_grade: str = "C30"
    seismic_grade: int = 2
    steel_price_per_ton: float = 4500.0   # 元/吨，用于节约额测算
    bars: list[BarItemReq]

    @field_validator("concrete_grade")
    @classmethod
    def valid_concrete(cls, v: str) -> str:
        if v not in FT_TABLE:
            raise ValueError(f"混凝土强度等级必须在 {list(FT_TABLE)} 中")
        return v

    @field_validator("seismic_grade")
    @classmethod
    def valid_seismic(cls, v: int) -> int:
        if v not in (1, 2, 3, 4):
            raise ValueError("抗震等级必须为 1/2/3/4")
        return v

    @field_validator("bars")
    @classmethod
    def at_least_one(cls, v: list) -> list:
        if not v:
            raise ValueError("至少录入一种钢筋")
        return v


# ── 参数读取工具 ────────────────────────────────────────────────

def _schema_default(key: str) -> float:
    item = next((s for s in ECONOMIC_PARAM_SCHEMA if s["key"] == key), None)
    return float(item["default"]) if item else 1.0


async def _load_economic_params(db) -> dict:
    rows = await db.fetch_all(
        "SELECT param_key, param_value FROM engine_params WHERE scope='economic'"
    )
    stored = {r["param_key"]: r["param_value"] for r in rows}

    def get(key: str) -> float:
        return float(stored[key]) if key in stored else _schema_default(key)

    std_lengths_raw = stored.get("standard_bar_lengths", "9000,12000")
    std_lengths = [int(x.strip()) for x in str(std_lengths_raw).split(",") if x.strip()]

    return {
        "seismic_factors": {
            1: get("seismic_factor_grade1"),
            2: get("seismic_factor_grade2"),
            3: get("seismic_factor_grade3"),
            4: get("seismic_factor_grade4"),
        },
        "lap_factors": {
            "25": get("lap_factor_25pct"),
            "50": get("lap_factor_50pct"),
            "100": get("lap_factor_100pct"),
        },
        "field_waste_rates": {
            "d6_10":    get("field_waste_rate_d6_10"),
            "d12_16":   get("field_waste_rate_d12_16"),
            "d18_22":   get("field_waste_rate_d18_22"),
            "d25_plus": get("field_waste_rate_d25_plus"),
        },
        "standard_lengths": std_lengths,
        "target_waste_rate":        get("target_waste_rate"),
        "auto_proposal_min_saving": get("auto_proposal_min_saving"),
    }


# ── 端点 ────────────────────────────────────────────────────────

@router.post("")
async def run_calc(
    drawing_id: str,
    body: CalcRequest,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    drawing = await db.fetch_one(
        "SELECT id, drawing_no, discipline FROM drawings WHERE id=$1", drawing_id
    )
    if not drawing:
        raise HTTPException(404, "图纸不存在")

    params = await _load_economic_params(db)

    # 计算各直径/级别组合的锚固长度
    bar_keys = {(b.diameter, b.steel_grade) for b in body.bars}
    anchor_results = [
        calc_anchor_lengths(
            diameter=d,
            steel_grade=g,
            concrete_grade=body.concrete_grade,
            seismic_grade=body.seismic_grade,
            seismic_factors=params["seismic_factors"],
            lap_factors=params["lap_factors"],
        )
        for d, g in sorted(bar_keys)
    ]

    # 下料优化
    bar_items = [
        BarItem(
            diameter=b.diameter,
            steel_grade=b.steel_grade,
            required_length=b.required_length,
            count=b.count,
        )
        for b in body.bars
    ]
    patterns, summary = optimize_cutting(
        bars=bar_items,
        standard_lengths=params["standard_lengths"],
        field_waste_rates=params["field_waste_rates"],
        steel_price_per_ton=body.steel_price_per_ton,
        target_waste_rate=params["target_waste_rate"],
        auto_proposal_min_saving=params["auto_proposal_min_saving"],
    )

    result_payload = {
        "drawing_id": drawing_id,
        "concrete_grade": body.concrete_grade,
        "seismic_grade": body.seismic_grade,
        "steel_price_per_ton": body.steel_price_per_ton,
        "anchor_lengths": [
            {
                "diameter": a.diameter,
                "steel_grade": a.steel_grade,
                "La": a.La,
                "LaE": a.LaE,
                "Ll_25": a.Ll_25,
                "Ll_50": a.Ll_50,
                "Ll_100": a.Ll_100,
            }
            for a in anchor_results
        ],
        "cutting_patterns": [
            {
                "standard_length": p.standard_length,
                "cuts": list(p.cuts),
                "waste": p.waste,
                "repeat": p.repeat,
            }
            for p in patterns
        ],
        **summary,
    }

    # 持久化（upsert）
    await db.execute(
        """INSERT INTO drawing_economic_calcs
             (drawing_id, calc_input, calc_result, created_by, created_at)
           VALUES ($1, $2, $3, $4, now())
           ON CONFLICT (drawing_id)
           DO UPDATE SET calc_input=$2, calc_result=$3, created_by=$4, created_at=now()""",
        drawing_id,
        json.dumps(body.model_dump()),
        json.dumps(result_payload),
        current_user["id"],
    )

    return result_payload


@router.get("")
async def get_calc(
    drawing_id: str,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    row = await db.fetch_one(
        "SELECT calc_result, created_at FROM drawing_economic_calcs WHERE drawing_id=$1",
        drawing_id,
    )
    if not row:
        return {"exists": False}
    return {**json.loads(row["calc_result"]), "calculated_at": row["created_at"]}
