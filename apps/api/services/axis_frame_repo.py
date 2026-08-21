"""Phase K-2：轴网帧的持久化与消费。

K-1 实测（`docs/PHASE_K_BLUEPRINT.md` §7）：二维联合聚类后
大歌剧院 **86%** 的图落在有交叉约束的帧里、残差中位 0.1 毫米、
P95 17 厘米；轨道交通 53%。

K-2 把帧落库，让构件坐标能从「各图自己的局部坐标系」换到
「工程自有坐标系」——**全程不需要一个测量坐标**
（实测有世界坐标的图只有 0.5%，轨道交通 0）。
"""
from __future__ import annotations

import json
from typing import Any

from services.axis_frame import AxisFrame


def build_frame_rows(project_id: str, story_key: str, building_unit: str,
                     frames: list[AxisFrame] | None) -> list[dict]:
    """帧列表 → `axis_frames` 入库行。

    **0 号是成员最多的主轴网**，下游默认取它；
    同一分组内可能有多套互不相容的轴网（分区工程一图三套，§8.33）。
    """
    ordered = sorted(frames or [], key=lambda f: -len(f.members))
    return [
        {
            "project_id": project_id,
            "story_key": story_key,
            "building_unit": building_unit or "-",
            "frame_index": index,
            "axes": json.dumps(frame.axes, ensure_ascii=False),
            "member_count": len(frame.members),
        }
        for index, frame in enumerate(ordered)
    ]


def build_placement_rows(frame_id: str, frame: AxisFrame | None,
                         registered: bool = False) -> list[dict]:
    """帧 → `drawing_frame_placements` 入库行。

    **没算出平移量的图不落库**：偏移 0 会被下游当成「已对齐」，
    而它其实是「没对齐」——判不出就说判不出。

    `frame_size` 必须带上：**单成员帧没有交叉约束**，
    一张图自己跟自己对齐残差恒 0 却不构成证据。
    消费方要能区分，否则「进帧率」会被灌水
    （实测含单成员时 98%，实际有约束的 86%）。
    """
    if frame is None or not frame.members:
        return []
    size = len(frame.members)
    rows = []
    for did in frame.members:
        offset = (frame.offsets or {}).get(did)
        if not offset or offset.get("x") is None or offset.get("y") is None:
            continue
        rows.append({
            "drawing_id": did,
            "frame_id": frame_id,
            "offset_x": float(offset["x"]),
            "offset_y": float(offset["y"]),
            "residual_m": (frame.residuals or {}).get(did),
            "frame_size": size,
            # **帧内一致 ≠ 帧间已配准**：K-1 的帧内残差毫米级、覆盖 86%，
            # 而 K-3 的帧间配准只覆盖 12%。两者必须能分开——
            # 此前对未配准的帧直接不落摆放，把 K-1 的成果一起丢了
            # （落库摆放 1394 → 171）。
            "registered": bool(registered),
        })
    return rows


def to_frame_coords(points: list | None, placement: Any) -> list:
    """构件坐标 → 帧内坐标。

    没有摆放信息时**原样返回**——不能悄悄挪到 0 点，
    那会让「没对齐的图」看起来像「对齐到原点的图」。
    """
    if placement is None:
        return list(points or [])
    data = dict(placement) if not isinstance(placement, dict) else placement
    dx, dy = float(data.get("offset_x") or 0.0), float(data.get("offset_y") or 0.0)
    out = []
    for point in points or []:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            out.append([float(point[0]) + dx, float(point[1]) + dy])
        else:
            out.append(point)
    return out


_DELETE_FRAMES_SQL = """
DELETE FROM axis_frames WHERE project_id = CAST(:project_id AS uuid)
"""

_INSERT_FRAME_SQL = """
INSERT INTO axis_frames
  (project_id, story_key, building_unit, frame_index, axes, member_count)
VALUES (CAST(:project_id AS uuid), :story_key, :building_unit,
        :frame_index, CAST(:axes AS jsonb), :member_count)
RETURNING id
"""

_INSERT_PLACEMENT_SQL = """
INSERT INTO drawing_frame_placements
  (drawing_id, frame_id, offset_x, offset_y, residual_m, frame_size,
   registered, updated_at)
VALUES (CAST(:drawing_id AS uuid), CAST(:frame_id AS uuid),
        :offset_x, :offset_y, :residual_m, :frame_size, :registered, now())
ON CONFLICT (drawing_id) DO UPDATE SET
  frame_id = EXCLUDED.frame_id, offset_x = EXCLUDED.offset_x,
  offset_y = EXCLUDED.offset_y, residual_m = EXCLUDED.residual_m,
  frame_size = EXCLUDED.frame_size, registered = EXCLUDED.registered,
  updated_at = now()
"""


async def persist_frames(db: Any, project_id: str,
                         grouped: dict) -> dict:
    """整项目落库。`grouped`: `{(story_key, unit): [AxisFrame, …]}`。

    **整项目覆盖式重写**：帧是从当前轴号数据算出来的，
    留着上一轮的帧会让「判据改进后被否掉的帧」永远活着
    （E1.5 的 supersedes 教训）。
    """
    await db.execute(_DELETE_FRAMES_SQL, {"project_id": project_id})
    frames_written = placed = with_constraint = registered_count = 0
    # **帧间配准**：每个帧以「本帧最小轴号 = 0」为原点，
    # 不配准的话 N 个帧就是 N 个互不相干的原点——实测构件换到帧内后
    # 包络/核心比不降反升（大歌剧院 3.99→4.85、轨道交通 3.05→8.42）。
    from services.axis_frame import register_frames_by_structure
    from services.frame_world_anchor import solve_frame_world_offset

    # **第一级：世界锚点**（强证据，Phase I 实测 RMSE 5.7 毫米）。
    # 实测 `axis_intersections` 全部带世界坐标，覆盖大歌剧院 76 张图、
    # 36 个帧，能钉住 445 张图（32%）——比纯靠帧间共有轴号的 12% 高一倍多。
    anchor_rows = await db.fetch_all(
        "SELECT drawing_id, label_x, label_y, world_x, world_y "
        "FROM axis_intersections WHERE project_id = CAST(:p AS uuid) "
        "AND world_x IS NOT NULL", {"p": project_id})
    anchors_by_drawing: dict = {}
    for row in anchor_rows:
        anchors_by_drawing.setdefault(str(row["drawing_id"]), []).append(dict(row))

    keyed = []
    for (story_key, unit), frames in (grouped or {}).items():
        for index, frame in enumerate(sorted(frames or [],
                                             key=lambda f: -len(f.members))):
            keyed.append(((story_key, unit or "-", index), frame))
    # 第一级：锚点钉住的帧
    pinned_by_index = {}
    for index, (_key, frame) in enumerate(keyed):
        pinned = solve_frame_world_offset(
            frame.axes,
            [a for m in frame.members for a in anchors_by_drawing.get(m, [])])
        if pinned and pinned.get("x") is not None and pinned.get("y") is not None:
            pinned_by_index[index] = pinned
    anchored = len(pinned_by_index)

    # 第二级：其余帧向**已钉住的帧**对齐（不是另起一个 0 点，
    # 否则两级配准落在不同参照系里，内容被隔开几公里）
    by_labels = register_frames_by_structure(keyed, seeds=pinned_by_index)
    registration = {id(f): by_labels[i] for i, (_k, f) in enumerate(keyed)}

    for (story_key, unit), frames in (grouped or {}).items():
        ordered = sorted(frames or [], key=lambda f: -len(f.members))
        rows = build_frame_rows(project_id, story_key, unit, ordered)
        for row, frame in zip(rows, ordered):
            record = await db.fetch_one(_INSERT_FRAME_SQL, row)
            frames_written += 1
            world = registration.get(id(frame))
            # 未配准的帧**照样落摆放**，只是标记 registered=false：
            # 帧内一致性（K-1，毫米级）与帧间配准（K-3）是两件事。
            for placement in build_placement_rows(
                    str(record["id"]), frame, registered=world is not None):
                placement["offset_x"] += float((world or {}).get("x") or 0.0)
                placement["offset_y"] += float((world or {}).get("y") or 0.0)
                await db.execute(_INSERT_PLACEMENT_SQL, placement)
                placed += 1
                if placement["frame_size"] >= 2:
                    with_constraint += 1
                if placement["registered"]:
                    registered_count += 1
    return {"frames": frames_written, "placed": placed,
            "anchored_frames": anchored,
            # **主口径是「有交叉约束的」**：单成员帧残差恒 0 却不构成证据
            "with_constraint": with_constraint,
            # 帧间已配准的——K-1 与 K-3 的分界，两者不可混为一谈
            "registered": registered_count}
