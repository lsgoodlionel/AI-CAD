"""轴网识别 API(Phase I 接入系统)。

- POST /projects/{id}/axis-recognition           全项目扇出识别
- GET  /projects/{id}/axis-recognition           每图摘要:有多少事等人处理
- POST /drawings/{id}/axis-recognition           单图识别(同步触发 Celery)
- GET  /drawings/{id}/axis-recognition           单图详情:分区/轴线/锚点/粗错/违规
- POST /drawings/{id}/axis-recognition/zones/{i} **确认分区编号**(§8.0.5 推不出)

**为什么要有这几个端点**:识别链路会产出三样**必须人看一眼**的东西——
分区编号、粗错坐标、国标校验违规。此前它们只存在于一次性脚本的 stdout 里,
等于没有交付。
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dependencies import get_current_user, get_db
from services import axis_recognition_repo as repo
from services.axis_zone_propagate_job import run_zone_propagation
from services.audit import write_audit
from tasks.axis_recognition import recognize_drawing_axes, recognize_project_axes

router = APIRouter(tags=["axis-recognition"])


class ZoneConfirmIn(BaseModel):
    """§8.0.5 未规定哪个分区是 1,几何推不出,只能由人确认。"""

    zone_label: str = Field(min_length=1, max_length=16)


async def _drawing_project(db: Any, drawing_id: str) -> str:
    row = await db.fetch_one(
        "SELECT project_id FROM drawings WHERE id = CAST(:id AS uuid)",
        {"id": drawing_id})
    if row is None:
        raise HTTPException(404, detail="DRAWING_NOT_FOUND")
    return str(row["project_id"])


@router.post("/projects/{project_id}/axis-recognition", status_code=202)
async def start_project_recognition(project_id: str, db=Depends(get_db),
                                    user=Depends(get_current_user)) -> dict:
    """全项目扇出。逐图独立执行,互不拖累。"""
    task = recognize_project_axes.delay(project_id)
    await write_audit(db, user_id=user["id"], action="axis_recognition.project",
                      resource="project", resource_id=project_id,
                      new_state={"task_id": str(task.id)})
    return {"success": True, "data": {"task_id": str(task.id)}}


@router.get("/projects/{project_id}/axis-recognition")
async def project_recognition_summary(project_id: str, db=Depends(get_db),
                                      _user=Depends(get_current_user)) -> dict:
    """每图一行的摘要,外加全项目「待处理」合计。"""
    rows = await repo.fetch_project_summary(db, project_id)
    pending = {
        "outliers": sum(r["outlier_count"] or 0 for r in rows),
        "violations": sum(r["violation_count"] or 0 for r in rows),
        "drawings": len(rows),
        "with_anchors": sum(1 for r in rows if (r["anchor_count"] or 0) > 0),
    }
    return {"success": True, "data": {"items": rows, "pending": pending}}


@router.post("/drawings/{drawing_id}/axis-recognition", status_code=202)
async def start_drawing_recognition(drawing_id: str, db=Depends(get_db),
                                    user=Depends(get_current_user)) -> dict:
    project_id = await _drawing_project(db, drawing_id)
    task = recognize_drawing_axes.delay(drawing_id)
    await write_audit(db, user_id=user["id"], action="axis_recognition.drawing",
                      resource="drawing", resource_id=drawing_id,
                      new_state={"task_id": str(task.id), "project_id": project_id})
    return {"success": True, "data": {"task_id": str(task.id)}}


@router.get("/drawings/{drawing_id}/axis-recognition")
async def drawing_recognition(drawing_id: str, db=Depends(get_db),
                              _user=Depends(get_current_user)) -> dict:
    """单图详情。未跑过返回 404,让前端显示「未识别」而不是空结果。"""
    result = await repo.fetch_result(db, drawing_id)
    if result is None:
        raise HTTPException(404, detail="AXIS_RECOGNITION_NOT_RUN")
    return {"success": True, "data": result}


@router.post("/drawings/{drawing_id}/axis-recognition/zones/{zone_index}")
async def confirm_zone_label(drawing_id: str, zone_index: int,
                             payload: ZoneConfirmIn, db=Depends(get_db),
                             user=Depends(get_current_user)) -> dict:
    """确认分区编号。**每个分区确认一次**,不是每条轴线。

    确认结果存在单独的表里,重跑识别会把它带回来,不被覆盖。
    """
    project_id = await _drawing_project(db, drawing_id)
    result = await repo.fetch_result(db, drawing_id)
    if result is None:
        raise HTTPException(404, detail="AXIS_RECOGNITION_NOT_RUN")
    if zone_index < 0 or zone_index >= len(result.get("zones") or []):
        raise HTTPException(400, detail="ZONE_INDEX_OUT_OF_RANGE")

    await repo.confirm_zone(db, project_id=project_id, drawing_id=drawing_id,
                            zone_index=zone_index,
                            zone_label=payload.zone_label,
                            confirmed_by=str(user["id"]))
    await write_audit(db, user_id=user["id"],
                      action="axis_recognition.confirm_zone",
                      resource="drawing", resource_id=drawing_id,
                      new_state={"zone_index": zone_index,
                                 "zone_label": payload.zone_label})
    # 分区号变了 → 轴号前缀跟着变,必须重跑一次识别才能落到锚点上
    recognize_drawing_axes.delay(drawing_id)
    return {"success": True, "data": {"zone_index": zone_index,
                                      "zone_label": payload.zone_label,
                                      "rerun": True}}


@router.post("/projects/{project_id}/axis-recognition/propagate-zones")
async def propagate_zones(project_id: str, db=Depends(get_db),
                          user=Depends(get_current_user)) -> dict:
    """把**人工确认**的分区号经轴距序列匹配传播到其他图（J1-3）。

    §8.0.5 的分区编号几何推不出，逐张确认 1052 张不现实。实测未匹配原因中
    「对不上任何锚」占 91%、歧义仅 1% ⇒ 瓶颈是锚覆盖不足而非算法，
    所以确认少数覆盖广的锚图、其余自动继承才是有杠杆的做法。

    **幂等**：每多确认一张锚图就再跑一次，匹配面扩一片。
    人工确认的行不会被覆盖。
    """
    stats = await run_zone_propagation(db, project_id)
    await write_audit(db, user_id=user["id"],
                      action="axis_recognition.propagate_zones",
                      resource="project", resource_id=project_id,
                      new_state=stats)
    return {"success": True, "data": stats}
