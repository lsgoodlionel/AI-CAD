"""Phase H4:装配编排 —— scene 楼层构件 → ComponentInstance 集(H2→H3→约束)。纯函数。

按楼层:观测适配(H2)→ 关联装配(H3)→ Z 恢复(楼层标高)→ 落轴网(有 axes 时);
跨层连续性作质量信号一并返回。持久化由调用方用 component_repository 落库。

输入 scene 为 model_builder.build_scene 产物(floors 带 elements/axes/elevation_m/order;
buildings 分组各含 floors)。纯函数、无 IO,离线可测。
"""
from __future__ import annotations

from typing import Any

from services.component_assembler import assemble_instances
from services.component_constraints import (
    apply_floor_z,
    check_vertical_continuity,
    snap_to_grid,
)
from services.component_dimension import snap_instances
from services.component_observations import observations_from_elements


def _floor_building_map(scene: dict) -> dict[str, str]:
    """floor.key → building.key(从 buildings 分组)。"""
    out: dict[str, str] = {}
    for building in scene.get("buildings") or []:
        for floor in building.get("floors") or []:
            key = floor.get("key")
            if key is not None:
                out[str(key)] = str(building.get("key") or "")
    return out


#: scene 楼层表 height_source → 构件 z_source(决定是否算「竖向真实」)。
#: default = 系统默认套层高(4.2/4.5),**不算真实**;manual/section/elevation 算真实。
_HEIGHT_SOURCE_TO_Z: dict[str, str] = {
    "section": "section",
    "elevation": "elevation",
    "manual": "manual",
    "default": "story_default",     # 默认套 → 不真实(H7 竖向真实率不计)
}


#: 竖向来源可信度排序(高→低),用于同层多单体时取最真实者
_Z_SOURCE_RANK = {"section": 3, "elevation": 3, "manual": 2, "story_default": 1}


def story_z_sources(scene: dict) -> dict[tuple[str, str], str]:
    """从 scene.quality.story_tables 取每层标高的**真实 provenance**。

    返回 {(building_key, story_key): z_source},并额外提供 ("", story_key) 兜底键:
    scene.floors 是**拍平**的(F1 只出现一次),而 story_tables 按单体分组,同一
    story_key 可能在多单体有不同来源 → 兜底键取**最真实**的那个(section>manual>default),
    避免真实来源被任意单体的 default 覆盖而低报。

    诚实依据:实测大歌剧院 north/south 全部 height_source=default(默认套 4.2/4.5),
    若一律记 floor_elevation 会让「竖向真实率」虚高到 1.0,掩盖标高其实是猜的。
    """
    out: dict[tuple[str, str], str] = {}
    best_by_story: dict[str, str] = {}
    tables = ((scene.get("quality") or {}).get("story_tables") or {})
    for bkey, levels in tables.items():
        for level in levels or []:
            skey = str(level.get("story_key") or "")
            if not skey:
                continue
            hsrc = str(level.get("height_source") or "").strip().lower()
            zsrc = _HEIGHT_SOURCE_TO_Z.get(hsrc, "story_default")
            out[(str(bkey), skey)] = zsrc
            prev = best_by_story.get(skey)
            if prev is None or _Z_SOURCE_RANK.get(zsrc, 0) > _Z_SOURCE_RANK.get(prev, 0):
                best_by_story[skey] = zsrc
    for skey, zsrc in best_by_story.items():
        out.setdefault(("", skey), zsrc)
    return out


def _compute_z_tops(floors: list[dict], floor_building: dict[str, str]) -> dict[str, float | None]:
    """每层 z_top = 同建筑下一层(按 order 升序)的 elevation_m;顶层为 None。"""
    groups: dict[str, list[dict]] = {}
    for floor in floors:
        bkey = floor_building.get(str(floor.get("key")), "")
        groups.setdefault(bkey, []).append(floor)
    tops: dict[str, float | None] = {}
    for floor_list in groups.values():
        ordered = sorted(floor_list, key=lambda f: f.get("order") or 0)
        for i, floor in enumerate(ordered):
            nxt = ordered[i + 1] if i + 1 < len(ordered) else None
            top = None
            if nxt is not None and nxt.get("elevation_m") is not None:
                top = float(nxt["elevation_m"])
            tops[str(floor.get("key"))] = top
    return tops


def assemble_scene_instances(
    scene: dict, vlm_obs_by_drawing: dict[str, list[dict]] | None = None,
) -> dict[str, Any]:
    """scene → {instances, continuity_gaps}。全项目 ComponentInstance 集 + 跨层缺口。

    vlm_obs_by_drawing(H5 职责 C 接线):{drawing_id: [观测]},由 VLM 语义分区区域转来
    (regions_to_observations),按楼层图纸并入装配,补几何引擎空洞。
    """
    floor_building = _floor_building_map(scene)
    floors = scene.get("floors") or []
    z_tops = _compute_z_tops(floors, floor_building)
    z_source_map = story_z_sources(scene)

    all_instances: list[dict] = []
    snapped_total = 0
    continuity_input: dict[str, list[tuple[int, list[dict]]]] = {}

    for floor in floors:
        elements = floor.get("elements") or {}
        axes = floor.get("axes")
        bkey = floor_building.get(str(floor.get("key")), "")
        order = int(floor.get("order") or 0)

        observations = observations_from_elements(elements, view_type="plan", axes=axes)
        # H5-C:该楼层图纸的 VLM 分区观测并入(补几何空洞)
        if vlm_obs_by_drawing:
            for d in floor.get("drawings") or []:
                did = str(d.get("id") or d.get("drawing_id") or "")
                observations.extend(vlm_obs_by_drawing.get(did) or [])
        instances = assemble_instances(
            observations, building_key=bkey, floor_key=str(floor.get("key") or ""))

        elevation = floor.get("elevation_m")
        if elevation is not None:
            # 竖向 provenance 取自 story_tables 真实来源;缺失时保守记 story_default
            # (宁可低报真实率,不虚高——默认套层高不得冒充真实标高)
            fkey = str(floor.get("key"))
            # 口径:**精确匹配该层所属单体**的来源;仅当精确缺失才用拍平兜底。
            # 结构性限制(如实):scene.floors 是拍平的,同名层(如 F1)被归到单一单体,
            # 而各单体来源可能不同(实测 main=manual 真实、north/south=default 默认套)
            # → 构件级无法精确区分,此处**保守取所属单体**(宁可低报真实率,不虚高)。
            z_src = (z_source_map.get((bkey, fkey))
                     or z_source_map.get(("", fkey))
                     or "story_default")
            apply_floor_z(
                instances, float(elevation), z_tops.get(str(floor.get("key"))), z_src,
            )
        if axes:
            snap_to_grid(instances, axes)
        # 真实度:截面模数化对齐(修几何提取的比例/像素抖动,实测柱宽种类 124→32)
        stat = snap_instances(instances)
        snapped_total += stat["snapped"]

        all_instances.extend(instances)
        continuity_input.setdefault(bkey, []).append((order, instances))

    gaps: list[dict] = []
    for floor_insts in continuity_input.values():
        gaps.extend(check_vertical_continuity(floor_insts))

    return {"instances": all_instances, "continuity_gaps": gaps,
            "dimension_snapped": snapped_total}
