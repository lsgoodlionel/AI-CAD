"""程序化 IFC 建模集成（Phase A · 灰度开关 model_ifc_enabled）。

从 ``services/model_builder.py`` 抽离，专职把 ``build_scene`` 的 scene 升级为
合规 IFC：scene → 合规 IFC4 → 上传 MinIO → That Open Fragments 转换/上传。

失败策略（与 build_scene 主流程一致）：
- 灰度关闭 / 无单体 / 纯贴图模式 → 返回 None（不建模）。
- Fragments 转换失败 → frag_key=None，但保留 IFC（不中断）。
- IFC 建模本身抛错 → 整体降级返回 None，绝不中断 build_scene。

蓝图：docs/MODEL_BASE_BLUEPRINT.md 第 4/7 节。
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from core.config import settings
from core.storage import upload_file

logger = logging.getLogger(__name__)

# 与 model_builder 一致：CPU/IO 密集的 IFC 组装放线程池，避免阻塞事件循环。
_executor = ThreadPoolExecutor(max_workers=2)


def _convert_fragments_quiet(ifc_bytes: bytes, project_id: str) -> str | None:
    """IFC → Fragments 上传；失败返回 None（前端回退 glTF/挤出，不中断）。"""
    try:
        from services.fragments_convert import convert_and_upload_ifc_bytes

        frag_key, _size = convert_and_upload_ifc_bytes(ifc_bytes, project_id, "project")
        return frag_key
    except Exception as exc:  # noqa: BLE001 — Fragments 失败降级保留 IFC/glTF
        logger.info("[ModelIfc] Fragments 转换降级(保留 IFC): %s", exc)
        return None


def _build_programmatic_ifc_sync(
    project_id: str, project_name: str, buildings: list[dict], floors: list[dict]
) -> dict:
    """线程池内同步执行：scene → 合规 IFC → 上传 → Fragments，返回 model_ifc 契约。"""
    from services.ifc_mapping import build_ifc_from_scene

    ifc_scene = {
        "project": {"id": str(project_id), "name": project_name},
        "buildings": buildings,
        "floors": floors,
    }
    ifc_bytes = build_ifc_from_scene(ifc_scene, project_name or None)
    ifc_key = f"projects/{project_id}/model_ifc/project.ifc"
    upload_file(ifc_bytes, ifc_key, "application/x-step")
    return {
        "ifc_key": ifc_key,
        "frag_key": _convert_fragments_quiet(ifc_bytes, project_id),
        "build_mode": "ifc",
        "is_estimated": True,  # Phase A 楼层标高为估算；Phase B z 恢复后转 False
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def maybe_build_programmatic_ifc(
    project_id: str, project_name: str,
    buildings: list[dict], floors: list[dict], reconstruction_mode: str,
) -> dict | None:
    """程序化 IFC 建模（灰度开关）。任何失败降级返回 None，绝不中断整体构建。"""
    if not settings.model_ifc_enabled or not buildings:
        return None
    if reconstruction_mode == "texture":
        return None  # 无确定性构件，无可建模几何
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            _executor, _build_programmatic_ifc_sync,
            project_id, project_name, buildings, floors,
        )
    except Exception as exc:  # noqa: BLE001 — IFC 建模失败降级，不中断构建
        logger.warning("[ModelIfc] 程序化 IFC 构建失败，降级挤出/贴图: %s", exc)
        return None
