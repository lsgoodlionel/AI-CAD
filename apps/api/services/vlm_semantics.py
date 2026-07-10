"""A-11 / A-13 · VLM 语义服务 + 语义 pipeline 融合。

**A-11**：`extract_drawing_semantics` 输入切图（A-12 `preprocess_for_vlm`），经
`ModelRouter.route("drawing_semantic_vlm", messages)` 抽取——

- 图名 `title`
- 标题栏字段 `title_block_fields`（图号 / 专业 / 比例 / 日期）
- 判专业 `discipline`（structure / architecture / mep / decoration）
- 跨图关联提示 `cross_hints`

每项均带 `confidence`，落 `DrawingSemanticResult`（frozen dataclass）。

**A-13**：把 VLM 结果**融入**现有确定性语义流（`drawing_semantics` /
`model_semantics` / `model_builder`）：

- `apply_vlm_discipline`：仅**补全空专业**，绝不覆盖确定性专业（冲突→确定性优先，
  VLM 仅留 `vlm_discipline_hint` 候选标注）。
- `merge_vlm_into_semantic_payload`：VLM 候选（`source="vlm"`）挂到 payload，
  低置信度进入 `unassigned_drawings`（现有审校队列）。
- `vlm_cross_link_candidates`：VLM 跨图提示 → 候选 `cross_links`（`source="vlm"`）。

────────────────────────────────────────────────────────────────────────
⚠️ 硬约束（VLM 语义边界）
    VLM 只做**语义与候选**（图名 / 标题栏 / 专业 / 跨图提示），
    **绝不产出任何计数 / 坐标 / 尺寸**——这些只走确定性几何管线。
    prompt 显式禁止，返回结构也不含任何数值几何。

⚠️ 离线 / CI：无网络、无 GPU、`vlm_semantic_enabled=False` 或未注入 router 时，
    走**确定性 mock**（不真调 LLM），保证 CI 可跑；任何解析/调用失败优雅降级
    （返回空/低置信度结果，绝不抛异常中断上层构建）。
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Mapping

from core.config import settings
from services.vlm_preprocess import preprocess_for_vlm

if TYPE_CHECKING:  # 仅类型标注，运行时不强依赖（router 由调用方注入）
    from core.llm.router import ModelRouter

logger = logging.getLogger(__name__)

VLM_ENGINE_NAME = "drawing_semantic_vlm"

# ── 融合阈值 ──────────────────────────────────────────────────
# 仅当 VLM 专业置信度 ≥ 此值，才用于**补全空专业**（确定性专业永不被覆盖）
VLM_DISCIPLINE_FILL_MIN = 0.60
# 低于此置信度的 VLM 项进入审校队列（SemanticReviewQueue）
LOW_CONFIDENCE_REVIEW_MAX = 0.60
# 单次构建最多送 VLM 的图纸数（保护超大套图，参考 MAX_TEXTURES_PER_PROJECT 思路）
MAX_VLM_DRAWINGS = 200

CANONICAL_DISCIPLINES = frozenset({"structure", "architecture", "mep", "decoration"})

# 专业别名 → 规范值（中英/图别名，用于 VLM 输出归一化与 mock 判专业）
_DISCIPLINE_ALIASES: dict[str, str] = {
    "structure": "structure", "结构": "structure", "结施": "structure", "s": "structure",
    "architecture": "architecture", "建筑": "architecture", "建施": "architecture", "a": "architecture",
    "mep": "mep", "机电": "mep", "给排水": "mep", "水施": "mep", "电气": "mep",
    "电施": "mep", "暖通": "mep", "暖施": "mep", "通风": "mep", "消防": "mep",
    "decoration": "decoration", "装修": "decoration", "装饰": "decoration",
    "精装": "decoration", "装施": "decoration",
}

# mock 判专业关键词（离线/CI 用，低置信度，不冒充真实模型）
_MOCK_DISCIPLINE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("结施", "structure"), ("结构", "structure"), ("配筋", "structure"),
    ("建施", "architecture"), ("建筑", "architecture"), ("门窗", "architecture"),
    ("水施", "mep"), ("电施", "mep"), ("暖施", "mep"), ("给排水", "mep"),
    ("电气", "mep"), ("暖通", "mep"), ("机电", "mep"), ("消防", "mep"),
    ("装修", "decoration"), ("装饰", "decoration"), ("精装", "decoration"),
)
_MOCK_CONFIDENCE = 0.55

# 图像 media type（切图输出恒为 PNG）
_IMAGE_MEDIA_TYPE = "image/png"

_VLM_PROMPT = (
    "你是施工图纸的语义识别助手。仅根据图片做**语义理解**，输出严格 JSON。\n"
    "任务：读出图名、标题栏字段（图号/专业/比例/日期）、判断专业、给出跨图关联提示。\n"
    "专业只能取其一：structure（结构）/ architecture（建筑）/ mep（机电）/ decoration（装修）。\n"
    "【硬约束】禁止输出任何构件计数、坐标、尺寸、长度、面积等数值几何信息——"
    "这些不可靠且非你的职责，只输出语义与候选。\n"
    "每个字段都要给 confidence（0~1 浮点，表示你的把握）。无法判断的字段留空字符串、confidence 给 0。\n"
    "只返回如下 JSON，不要额外解释：\n"
    "{\n"
    '  "title": {"value": "", "confidence": 0.0},\n'
    '  "discipline": {"value": "structure", "confidence": 0.0},\n'
    '  "title_block": {\n'
    '    "drawing_no": {"value": "", "confidence": 0.0},\n'
    '    "scale": {"value": "", "confidence": 0.0},\n'
    '    "date": {"value": "", "confidence": 0.0}\n'
    "  },\n"
    '  "cross_hints": [{"value": "", "confidence": 0.0}]\n'
    "}"
)


# ── 数据结构（frozen / 不可变）─────────────────────────────────

@dataclass(frozen=True)
class VlmValue:
    """带置信度的单值。value 为空串表示 VLM 未给出。"""
    value: str
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "confidence": self.confidence}


@dataclass(frozen=True)
class DrawingSemanticResult:
    """A-11 输出契约（frozen）。仅语义与候选，不含任何计数/坐标/尺寸。

    ``source``：``vlm``（真实模型）/ ``mock``（离线降级）/ ``disabled``（开关关闭）/
    ``error``（调用或解析失败降级）。
    """
    drawing_id: str = ""
    title: VlmValue | None = None
    discipline: VlmValue | None = None          # 规范值 structure/architecture/mep/decoration
    drawing_no: VlmValue | None = None
    scale: VlmValue | None = None
    date: VlmValue | None = None
    cross_hints: tuple[VlmValue, ...] = ()
    source: str = "vlm"

    @property
    def is_empty(self) -> bool:
        """无任何有效语义（供上层判断是否需要收录/审校）。"""
        return not any(
            (field and field.value)
            for field in (self.title, self.discipline, self.drawing_no, self.scale, self.date)
        ) and not self.cross_hints

    @property
    def title_block_fields(self) -> dict[str, dict[str, Any]]:
        """标题栏字段字典（图号/专业/比例/日期），仅含 VLM 给出的项。"""
        fields: dict[str, dict[str, Any]] = {}
        for name, field in (
            ("drawing_no", self.drawing_no),
            ("discipline", self.discipline),
            ("scale", self.scale),
            ("date", self.date),
        ):
            if field and field.value:
                fields[name] = field.as_dict()
        return fields

    def as_dict(self) -> dict[str, Any]:
        return {
            "drawing_id": self.drawing_id,
            "title": self.title.as_dict() if self.title else None,
            "discipline": self.discipline.as_dict() if self.discipline else None,
            "title_block_fields": self.title_block_fields,
            "cross_hints": [hint.as_dict() for hint in self.cross_hints],
            "source": self.source,
        }


# ── A-11：VLM 语义抽取 ─────────────────────────────────────────

async def extract_drawing_semantics(
    drawing: Mapping[str, Any],
    *,
    image_bytes: bytes | None = None,
    ext: str = "",
    router: "ModelRouter | None" = None,
) -> DrawingSemanticResult:
    """抽取单张图纸语义（A-11）。绝不抛异常，失败一律优雅降级。

    降级路径：
    - ``vlm_semantic_enabled=False`` → ``source="disabled"`` 空结果，不调用任何模型。
    - 未注入 ``router`` / 无图片字节 / 切图无有效图像 → 确定性 ``mock``（供 CI）。
    - 模型调用或 JSON 解析失败 → ``source="error"`` 空结果。
    """
    drawing_id = _drawing_id(drawing)

    if not settings.vlm_semantic_enabled:
        return DrawingSemanticResult(drawing_id=drawing_id, source="disabled")

    if router is None or not image_bytes:
        return _mock_result(drawing, drawing_id)

    try:
        crops = preprocess_for_vlm(image_bytes, ext)
        images = _collect_images(crops)
        if not images:
            logger.info("[vlm_semantics] 切图无有效图像，降级 mock drawing=%s", drawing_id)
            return _mock_result(drawing, drawing_id)
        messages = _build_messages(images)
        response = await router.route(VLM_ENGINE_NAME, messages)
        parsed = _parse_vlm_json(getattr(response, "content", "") or "")
        return _result_from_parsed(drawing_id, parsed, source="vlm")
    except Exception as exc:  # noqa: BLE001 — VLM 失败必须降级，绝不中断构建
        logger.warning("[vlm_semantics] VLM 抽取失败 drawing=%s: %s", drawing_id, exc)
        return DrawingSemanticResult(drawing_id=drawing_id, source="error")


def _collect_images(crops: Mapping[str, Any]) -> list[bytes]:
    """从切图结果收集非空 PNG（标题栏优先，其次整图缩略图）。"""
    images: list[bytes] = []
    for key in ("title_block_png", "overview_png"):
        png = crops.get(key)
        if isinstance(png, (bytes, bytearray)) and png:
            images.append(bytes(png))
    return images


def _build_messages(images: list[bytes]) -> list[dict[str, Any]]:
    """构造多模态 user 消息：文本 prompt + base64 图像块（vision.py 规范格式）。"""
    import base64

    content: list[dict[str, Any]] = [{"type": "text", "text": _VLM_PROMPT}]
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


def _parse_vlm_json(raw: str) -> dict[str, Any]:
    """健壮解析 VLM 输出为 dict：剥离 markdown 围栏、截取首尾大括号、json.loads。

    失败返回空 dict（上层据此产出空结果）。
    """
    text = raw.strip()
    if not text:
        return {}
    # 去掉 ```json ... ``` 围栏
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _result_from_parsed(
    drawing_id: str, parsed: Mapping[str, Any], *, source: str
) -> DrawingSemanticResult:
    """解析后的 dict → DrawingSemanticResult，字段缺失/类型异常均容错。"""
    title_block = parsed.get("title_block")
    title_block = title_block if isinstance(title_block, Mapping) else {}
    return DrawingSemanticResult(
        drawing_id=drawing_id,
        title=_value_of(parsed.get("title")),
        discipline=_discipline_value(parsed.get("discipline")),
        drawing_no=_value_of(title_block.get("drawing_no")),
        scale=_value_of(title_block.get("scale")),
        date=_value_of(title_block.get("date")),
        cross_hints=_hints_of(parsed.get("cross_hints")),
        source=source,
    )


def _value_of(raw: Any) -> VlmValue | None:
    """{"value","confidence"} → VlmValue；空值返回 None。置信度夹到 [0,1]。"""
    if not isinstance(raw, Mapping):
        return None
    value = raw.get("value")
    if not isinstance(value, str) or not value.strip():
        return None
    return VlmValue(value=value.strip(), confidence=_clamp(raw.get("confidence")))


def _discipline_value(raw: Any) -> VlmValue | None:
    """专业值归一化到规范枚举；非法专业丢弃（防幻觉）。"""
    field = _value_of(raw)
    if field is None:
        return None
    canonical = _normalize_discipline(field.value)
    if canonical is None:
        return None
    return VlmValue(value=canonical, confidence=field.confidence)


def _hints_of(raw: Any) -> tuple[VlmValue, ...]:
    if not isinstance(raw, list):
        return ()
    hints: list[VlmValue] = []
    for item in raw:
        field = _value_of(item)
        if field is not None:
            hints.append(field)
    return tuple(hints)


def _normalize_discipline(value: str) -> str | None:
    key = value.strip().lower()
    if key in CANONICAL_DISCIPLINES:
        return key
    return _DISCIPLINE_ALIASES.get(key) or _DISCIPLINE_ALIASES.get(value.strip())


def _clamp(raw: Any) -> float:
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def _drawing_id(drawing: Mapping[str, Any]) -> str:
    return str(drawing.get("id") or drawing.get("drawing_id") or "")


def _mock_result(drawing: Mapping[str, Any], drawing_id: str) -> DrawingSemanticResult:
    """离线/CI 确定性 mock：由图名/文件名文本判专业（低置信度，不冒充真实模型）。"""
    text = " ".join(
        str(drawing.get(key) or "")
        for key in ("title", "filename", "drawing_no", "folder_path")
    )
    discipline: VlmValue | None = None
    for keyword, canonical in _MOCK_DISCIPLINE_KEYWORDS:
        if keyword in text:
            discipline = VlmValue(value=canonical, confidence=_MOCK_CONFIDENCE)
            break
    title_text = str(drawing.get("title") or "").strip()
    title = VlmValue(value=title_text, confidence=_MOCK_CONFIDENCE) if title_text else None
    return DrawingSemanticResult(
        drawing_id=drawing_id, title=title, discipline=discipline, source="mock"
    )


# ── A-13：场景级 VLM 收集（灰度门控）───────────────────────────

async def collect_scene_vlm(
    db, drawings: list[dict], *, router: "ModelRouter | None" = None
) -> dict[str, DrawingSemanticResult]:
    """为一批图纸收集 VLM 语义（A-13 接线）。

    **门控**：``vlm_semantic_enabled=False`` → 立即返回 ``{}``（不建 router、不查库、
    不读文件），保证开关关闭时 ``build_scene`` 行为与现网逐字节等价。

    开启时：构建 router（未注入则从 Redis 自建），逐图 MinIO 取字节 → 抽取语义。
    单图失败跳过；router 构建失败整体降级 ``{}``；绝不抛异常。
    """
    if not settings.vlm_semantic_enabled or not drawings:
        return {}

    router = router or _build_router(db)
    if router is None:
        return {}

    from core.storage import get_file_bytes

    results: dict[str, DrawingSemanticResult] = {}
    for drawing in drawings[:MAX_VLM_DRAWINGS]:
        drawing_id = _drawing_id(drawing)
        file_key = str(drawing.get("file_key") or "")
        if not drawing_id or not file_key:
            continue
        try:
            image_bytes = get_file_bytes(file_key)
            result = await extract_drawing_semantics(
                drawing, image_bytes=image_bytes, ext=_ext_of(file_key), router=router
            )
        except Exception as exc:  # noqa: BLE001 — 单图失败跳过，不影响其余
            logger.warning("[vlm_semantics] 图纸 VLM 收集失败 drawing=%s: %s", drawing_id, exc)
            continue
        if not result.is_empty:
            results[drawing_id] = result
    return results


def _build_router(db) -> "ModelRouter | None":
    """从共享 db + Redis 构建 ModelRouter；失败返回 None（降级）。"""
    try:
        from redis.asyncio import Redis

        from core.llm.router import ModelRouter

        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        return ModelRouter(db=db, redis=redis)
    except Exception as exc:  # noqa: BLE001 — 无 Redis/依赖缺失 → 降级不建模
        logger.warning("[vlm_semantics] 构建 ModelRouter 失败，降级跳过 VLM: %s", exc)
        return None


def _ext_of(file_key: str) -> str:
    _, _, ext = file_key.rpartition(".")
    return ext.lower() if ext and ext != file_key else ""


# ── A-13：融合（纯函数，确定性优先仲裁）────────────────────────

def apply_vlm_discipline(
    drawings: list[dict], vlm_by_drawing: Mapping[str, DrawingSemanticResult]
) -> list[dict]:
    """用 VLM 专业**补全空专业**，返回新列表（不可变）。

    **确定性优先仲裁**：
    - 图纸已有确定性专业 → 永不覆盖；若 VLM 专业不同，仅记 ``vlm_discipline_hint``
      候选标注（保留 ``source="vlm"`` + 置信度）。
    - 图纸专业为空且 VLM 置信度 ≥ ``VLM_DISCIPLINE_FILL_MIN`` → 补全，标
      ``discipline_source="vlm"``。

    ``vlm_by_drawing`` 为空 → 原样返回（同一对象，保证零差异回归）。
    """
    if not vlm_by_drawing:
        return drawings

    updated: list[dict] = []
    for drawing in drawings:
        result = vlm_by_drawing.get(_drawing_id(drawing))
        updated.append(_apply_one_discipline(drawing, result))
    return updated


def _apply_one_discipline(
    drawing: dict, result: DrawingSemanticResult | None
) -> dict:
    if result is None or result.discipline is None:
        return drawing
    vlm_disc = result.discipline
    existing = str(drawing.get("discipline") or "").strip()
    if existing:
        if _normalize_discipline(existing) != vlm_disc.value:
            # 冲突：确定性优先，VLM 仅作候选标注
            return {
                **drawing,
                "vlm_discipline_hint": {
                    "value": vlm_disc.value,
                    "confidence": vlm_disc.confidence,
                    "source": "vlm",
                },
            }
        return drawing
    if vlm_disc.confidence < VLM_DISCIPLINE_FILL_MIN:
        return drawing  # 置信度不足，不补全（进审校队列由 merge 处理）
    return {
        **drawing,
        "discipline": vlm_disc.value,
        "discipline_source": "vlm",
        "discipline_confidence": vlm_disc.confidence,
    }


def merge_vlm_into_semantic_payload(
    payload: dict, vlm_by_drawing: Mapping[str, DrawingSemanticResult]
) -> dict:
    """把 VLM 候选融入 `_semantic_scene_payload` 输出，返回新 payload。

    - 新增 ``vlm_candidates``：每图 VLM 结果（``source="vlm"``），供语义树/审校展示。
    - 低置信度 VLM 项（专业/图名 < ``LOW_CONFIDENCE_REVIEW_MAX``）追加进
      ``unassigned_drawings``（现有 SemanticReviewQueue），去重。
    - 确定性 ``semantic_tree`` 原样保留，绝不被 VLM 覆盖。

    ``vlm_by_drawing`` 为空 → 原样返回（零差异回归）。
    """
    if not vlm_by_drawing:
        return payload

    candidates = [result.as_dict() for result in vlm_by_drawing.values()]
    review_items = _vlm_review_items(payload, vlm_by_drawing)
    unassigned = [*payload.get("unassigned_drawings", []), *review_items]
    return {
        **payload,
        "vlm_candidates": candidates,
        "unassigned_drawings": unassigned,
    }


def _vlm_review_items(
    payload: dict, vlm_by_drawing: Mapping[str, DrawingSemanticResult]
) -> list[dict[str, Any]]:
    """低置信度 VLM 项 → 审校队列条目（对已有 unassigned 去重）。"""
    existing_ids = {
        str(item.get("drawing_id") or "")
        for item in payload.get("unassigned_drawings", [])
    }
    items: list[dict[str, Any]] = []
    for drawing_id, result in vlm_by_drawing.items():
        if drawing_id in existing_ids:
            continue
        if not _has_low_confidence(result):
            continue
        items.append({
            "drawing_id": drawing_id,
            "reason": "vlm_low_confidence",
            "source": "vlm",
            "vlm": result.as_dict(),
        })
    return items


def _has_low_confidence(result: DrawingSemanticResult) -> bool:
    for field in (result.discipline, result.title):
        if field and field.value and 0 < field.confidence < LOW_CONFIDENCE_REVIEW_MAX:
            return True
    return False


def vlm_cross_link_candidates(
    vlm_by_drawing: Mapping[str, DrawingSemanticResult],
    ids_by_no: Mapping[str, list[str]],
    floor_by_no: Mapping[str, str],
) -> list[dict[str, Any]]:
    """VLM 跨图提示 → 候选 cross_links（``source="vlm"``，仅提示不裁决）。

    为空 → 返回 ``[]``（零差异回归）。
    """
    if not vlm_by_drawing:
        return []
    links: list[dict[str, Any]] = []
    for drawing_id, result in vlm_by_drawing.items():
        for hint in result.cross_hints:
            if not hint.value:
                continue
            links.append({
                "kind": "VLM提示",
                "label": hint.value,
                "confidence": hint.confidence,
                "source": "vlm",
                "floor_keys": [],
                "drawing_ids": [drawing_id],
            })
    return links
