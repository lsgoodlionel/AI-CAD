"""图框上印的比例：从图纸信息档案（OCR）读出来，挂到图纸上交给识别器。

**为什么需要**：这批 PDF 的文字是轮廓化的，识别器读矢量文字几乎读不到
`1:N`，落库变换又常是推断出来的垃圾值（被门禁挡掉），于是大量平面图落到
**缺省 1:100**。而档案 OCR 早就读到了图框里的比例 —— 全库 2439 张图有。

**只用 OCR 条目**：矢量文字条目的 bbox 是页面点，OCR 条目在 2026-08-31 之前
是另一套缩放框，两者混在一起无法统一归一化；矢量文字识别器本就自己读。

排序与可信度见 `core.model3d.element_recognizer.resolve_scale`，
取值规则见 `core.model3d.scale_evidence.printed_scale_from_archive`。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.model3d.scale_evidence import printed_scale_from_archive

logger = logging.getLogger(__name__)

#: 挂在图纸 dict 上的键名（m/pt）。
PRINTED_SCALE_KEY = "printed_scale_m_pt"

#: 自动 OCR 行（有效的）+ 人工核定行。人审修正会把对应自动行置为无效
#: （migration 030 的 auto/verified 分离），**核定值永远优先**。
_SCALE_ROWS_SQL = r"""
SELECT drawing_id::text AS did, content, location_json, source_kind
FROM drawing_extracted_info
WHERE project_id = CAST(:pid AS uuid) AND is_active
  AND (extractor = 'ocr' OR source_kind = 'verified')
  AND content ~ '1\s*[:：]\s*[0-9]'
"""

#: 本图全部自动 OCR 词条的范围 —— 位置归一化用（见 printed_scale_from_archive）。
_EXTENT_SQL = """
SELECT drawing_id::text AS did,
       max((location_json->'bbox'->>2)::float) AS mx,
       max((location_json->'bbox'->>3)::float) AS my
FROM drawing_extracted_info
WHERE project_id = CAST(:pid AS uuid) AND extractor = 'ocr'
  AND source_kind = 'auto' AND is_active AND location_json ? 'bbox'
GROUP BY drawing_id
"""


async def load_printed_scales(db: Any, project_id: str) -> dict[str, float]:
    """drawing_id → 图框印刷比例（m/pt）。档案不可用时返回 {}，建模照旧。"""
    try:
        extent_rows = await db.fetch_all(_EXTENT_SQL, {"pid": project_id})
        rows = await db.fetch_all(_SCALE_ROWS_SQL, {"pid": project_id})
    except Exception as exc:  # noqa: BLE001 — 档案缺失不阻断建模，但必须看得见
        # warning 而不是 info：这条通道静默失效时，所有图都会退回缺省比例。
        logger.warning("[printed_scale] 档案不可用，本次建模不用图框比例: %s", exc)
        return {}
    extent = {str(r["did"]): (r["mx"], r["my"]) for r in extent_rows}
    auto: dict[str, list[dict]] = {}
    verified: dict[str, list[dict]] = {}
    for r in rows:
        bucket = verified if r["source_kind"] == "verified" else auto
        bucket.setdefault(str(r["did"]), []).append(
            {"content": r["content"], "location_json": _as_json(r["location_json"])})
    scales: dict[str, float] = {}
    for did, items in auto.items():
        scale = printed_scale_from_archive(items, extent.get(did))
        if scale is not None:
            scales[did] = scale
    for did, items in verified.items():
        # 人核过的比例不论位置、直接采用（不参与与自动行的计票）
        scale = printed_scale_from_archive([{**i, "location_json": None} for i in items])
        if scale is not None:
            scales[did] = scale
    return scales


def with_printed_scales(drawings: list[dict], scales: dict[str, float]) -> list[dict]:
    """返回挂上印刷比例的**新**图纸列表；没读到的图原样保留。"""
    if not scales:
        return list(drawings)
    return [
        {**d, PRINTED_SCALE_KEY: scales[str(d.get("id"))]}
        if str(d.get("id")) in scales else d
        for d in drawings
    ]


def _as_json(value: Any) -> Any:
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value
