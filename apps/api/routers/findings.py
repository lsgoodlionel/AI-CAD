"""Finding 统一聚合 API（Phase D · 泳道2 · D-05；创效线索转化 D-07）。

把五类割裂的问题/发现（单图 AI 审图 / 会审 / 跨图套图 / 语义审校 / 符号待审）
统一读取为一个 Finding 抽象，供「审查中心」（D-06）等前端组件一处消费。

端点（统一信封 ``{success, data, error, meta}``）：
- GET  /projects/{project_id}/findings
      列表：source/severity/status/drawing_id 筛选 + 分页 + 汇总统计（meta，含
      ``saving_potential_count``）
- GET  /projects/{project_id}/findings/{source}/{source_key}
      单条详情（含 ``has_saving_potential`` 规则判别标）
- POST /projects/{project_id}/findings/{source}/{source_key}/status
      状态流转：pending → acknowledged → remediated → closed（单向，不可回退）
- POST /projects/{project_id}/findings/{source}/{source_key}/to-proposal
      （D-07）一键转创效提案**草稿**：规则优先判别创效潜力，``use_llm=True`` 时可选
      走 ``ModelRouter`` 增强召回；仅造 draft，三审签字硬约束不被绕过（同
      ``routers/project_models.py`` 的 ``qto_to_proposal`` 模式）

router 变量名：``router``；建议注册前缀 ``/api/v1``（与其它 routers 一致，
挂载方式对齐 routers/model_review.py：``app.include_router(router, prefix="/api/v1")``）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.llm.router import ModelRouter
from dependencies import get_current_user, get_db, get_router
from services import finding_service
from services.audit import write_audit

router = APIRouter(prefix="/projects", tags=["findings"])


class FindingStatusUpdate(BaseModel):
    status: str = Field(..., description="pending|acknowledged|remediated|closed")
    note: str | None = Field(None, max_length=2000)


class FindingToProposalBody(BaseModel):
    """D-07：Finding → 创效提案草稿请求体。全部字段可选，缺省即用 Finding 原生内容。"""
    title: str | None = Field(None, min_length=2, max_length=300)
    proposal_type: str = Field("A", pattern="^[AB]$", description="A=直接降本 B=间接增收")
    raw_saving_est: float | None = Field(None, ge=0, description="预估净节约（元），待经济师测算复核")
    note: str | None = Field(None, max_length=1000, description="附加说明，写入草稿描述")
    use_llm: bool = Field(False, description="规则未命中创效潜力时，是否尝试 LLM 可选增强判别")


def _require_valid_source(source: str) -> None:
    if source not in finding_service.VALID_SOURCES:
        raise HTTPException(status_code=400, detail="INVALID_SOURCE")


@router.get("/{project_id}/findings")
async def list_findings(
    project_id: str,
    source: str | None = Query(None, description="engine|review|cross|semantic|symbol"),
    severity: str | None = Query(None, description="critical|high|medium|low"),
    status: str | None = Query(None, description="pending|acknowledged|remediated|closed"),
    drawing_id: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """项目全部 Finding 列表（跨五类来源），带筛选与汇总统计。"""
    if source is not None:
        _require_valid_source(source)
    if severity is not None and severity not in finding_service.VALID_SEVERITIES:
        raise HTTPException(status_code=400, detail="INVALID_SEVERITY")
    if status is not None and status not in finding_service.STATUS_ORDER:
        raise HTTPException(status_code=400, detail="INVALID_STATUS")

    items, summary = await finding_service.list_findings(
        db, project_id,
        source=source, severity=severity, status=status, drawing_id=drawing_id,
        limit=limit, offset=offset,
    )
    return {
        "success": True,
        "data": items,
        "error": None,
        "meta": {**summary, "limit": limit, "offset": offset},
    }


@router.get("/{project_id}/findings/{source}/{source_key}")
async def get_finding(
    project_id: str,
    source: str,
    source_key: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """单条 Finding 详情。"""
    _require_valid_source(source)

    finding = await finding_service.get_finding(db, project_id, source, source_key)
    if finding is None:
        raise HTTPException(status_code=404, detail="FINDING_NOT_FOUND")
    return {"success": True, "data": finding, "error": None, "meta": {}}


@router.post("/{project_id}/findings/{source}/{source_key}/status")
async def transition_finding_status(
    project_id: str,
    source: str,
    source_key: str,
    body: FindingStatusUpdate,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """推进 Finding 闭环状态机：待处理→已确认→已整改→已闭环（单向，不可回退）。

    注：finding id 非全局 UUID（如 "symbol:1" / "semantic:host:o1"），故审计日志
    ``resource_id`` 留空，finding id 写入 ``new_state`` JSON（对齐 audit_logs.resource_id
    为 UUID 类型的既有 schema 约束，避免非 UUID 字符串写入报错）。
    """
    _require_valid_source(source)
    if body.status not in finding_service.STATUS_ORDER:
        raise HTTPException(status_code=400, detail="INVALID_STATUS")

    try:
        result = await finding_service.update_finding_status(
            db,
            project_id=project_id, source=source, source_key=source_key,
            target_status=body.status, note=body.note,
            user_id=current_user.get("id"),
        )
    except finding_service.InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await write_audit(
        db, user_id=current_user.get("id"),
        action="finding_status_transition", resource="finding", resource_id=None,
        new_state={
            "finding_id": f"{source}:{source_key}", "project_id": project_id,
            "status": body.status, "note": body.note,
        },
    )

    return {
        "success": True,
        "data": {
            "id": f"{source}:{source_key}",
            "status": result.get("status"),
            "note": result.get("note"),
            "status_updated_at": result.get("updated_at"),
        },
        "error": None,
        "meta": {"project_id": project_id},
    }


@router.post("/{project_id}/findings/{source}/{source_key}/to-proposal", status_code=201)
async def finding_to_proposal(
    project_id: str,
    source: str,
    source_key: str,
    body: FindingToProposalBody,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    """（D-07）Finding → 创效提案草稿。仅造 ``draft``，三审签字硬约束不被绕过。

    创效潜力判别（规则优先，见 ``finding_service.assess_saving_potential``）：
    - 默认（``use_llm=False``）只用规则；规则未命中 → 409 拒绝创建。
    - ``use_llm=True`` 且规则未命中 → 尝试 ``ModelRouter`` 增强判别（引擎未配置/调用
      失败一律降级为规则结果，不阻塞本请求）；LLM 仍判无潜力 → 409。

    插入 ``incentive_proposals`` 的方式与 ``routers/project_models.py`` 的
    ``qto_to_proposal`` 同一模式：直接 INSERT status 默认 draft，不做任何测算/签字。
    """
    _require_valid_source(source)

    finding = await finding_service.get_finding(db, project_id, source, source_key)
    if finding is None:
        raise HTTPException(status_code=404, detail="FINDING_NOT_FOUND")

    assessment = await finding_service.assess_saving_potential(
        finding, router=model_router if body.use_llm else None,
    )
    if not assessment["has_saving_potential"]:
        raise HTTPException(status_code=409, detail="NO_SAVING_POTENTIAL")

    description = finding_service.build_finding_proposal_description(finding, body.note)
    inserted = await db.fetch_one(
        """
        INSERT INTO incentive_proposals
            (project_id, drawing_id, proposer_id, proposal_type,
             title, description, raw_saving_est)
        VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
        """,
        project_id, finding.get("drawing_id"), current_user["id"], body.proposal_type,
        body.title or finding["title"], description, body.raw_saving_est,
    )
    proposal_id = str(inserted["id"])

    await write_audit(
        db, user_id=current_user.get("id"), action="finding_to_proposal",
        resource="proposal", resource_id=proposal_id,
        new_state={
            "finding_id": finding["id"], "project_id": project_id,
            "saving_assessment_source": assessment["source"],
        },
        ip_address=request.client.host if request.client else None,
    )

    return {
        "success": True,
        "data": {
            "proposal_id": proposal_id,
            "status": "draft",
            "finding_id": finding["id"],
            "saving_assessment": assessment,
        },
        "error": None,
        "meta": {"project_id": project_id},
    }
