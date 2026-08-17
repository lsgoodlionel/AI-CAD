"""Phase H5 职责 C:无图层 PDF 语义分区(蓝图 §4 C)——用 VLM 看图标出各类构件所在
区域,补几何引擎在无矢量/无图层时的空洞。产出区域候选(归一化 bbox),供装配作
Observation 源(补配准短板)。

- build_segmentation_messages / parse_segmentation 纯函数,离线可测;
- segment_regions 经 ModelRouter 调 VLM,失败降级(available=False),绝不中断。

区域形如 {"type":"column","bbox":[x,y,w,h],"confidence":0.8}(bbox 归一化 [0,1])。
"""
from __future__ import annotations

import base64
import json
from typing import Any

SEGMENT_ENGINE = "drawing_semantic_vlm"
_IMAGE_MEDIA_TYPE = "image/png"

_VALID_TYPES = ("column", "pile", "wall", "beam", "slab", "pipe", "equipment", "door", "window")

_PROMPT = (
    "这是一张无图层的施工图。请标出图中各类构件所在的矩形区域(归一化坐标 0~1)。"
    "只输出 JSON:{\"regions\":[{\"type\":\"<类型>\",\"bbox\":[x,y,w,h],\"confidence\":0~1}]}。"
    "类型取值:" + "/".join(_VALID_TYPES) + "。无法确定的不要编造。"
)


def build_segmentation_messages(images: list[bytes]) -> list[dict[str, Any]]:
    """图像字节 → VLM 多模态 messages(文本 prompt + base64 图,对齐 vlm_semantics 格式)。"""
    content: list[dict[str, Any]] = [{"type": "text", "text": _PROMPT}]
    for png in images:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _IMAGE_MEDIA_TYPE,
                "data": base64.b64encode(png).decode("ascii"),
            },
        })
    return [{"role": "user", "content": content}]


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


def _valid_bbox(bbox: Any) -> list[float] | None:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        vals = [float(v) for v in bbox]
    except (ValueError, TypeError):
        return None
    # 归一化范围内(容忍轻微越界),宽高需正
    if any(v < -0.05 or v > 1.05 for v in vals) or vals[2] <= 0 or vals[3] <= 0:
        return None
    return [round(v, 4) for v in vals]


def parse_segmentation(text: str) -> dict:
    """VLM 文本 → {available, regions}。过滤非法类型/bbox,防脏值污染装配。"""
    obj = _extract_json(text)
    if obj is None:
        return {"available": False, "regions": []}
    raw = obj.get("regions")
    if not isinstance(raw, list):
        return {"available": False, "regions": []}
    regions: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ctype = str(item.get("type") or "").strip().lower()
        if ctype not in _VALID_TYPES:
            continue
        bbox = _valid_bbox(item.get("bbox"))
        if bbox is None:
            continue
        conf = item.get("confidence")
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except (ValueError, TypeError):
            conf = 0.5
        regions.append({"type": ctype, "bbox": bbox, "confidence": round(conf, 3)})
    return {"available": bool(regions), "regions": regions}


async def segment_regions(images: list[bytes], router: Any) -> dict:
    """经 VLM 语义分区。router 为 None / 无图 / 失败 → available=False(降级)。"""
    if router is None or not images:
        return {"available": False, "regions": []}
    try:
        messages = build_segmentation_messages(images)
        response = await router.route(SEGMENT_ENGINE, messages)
        return parse_segmentation(getattr(response, "content", "") or "")
    except Exception:  # noqa: BLE001 — VLM 失败降级
        return {"available": False, "regions": []}
