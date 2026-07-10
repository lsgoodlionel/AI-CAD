"""A-11 / A-13 · VLM 语义服务 + pipeline 融合测试。

覆盖：
- A-11 抽取：开关关闭不调用 / 无 router 走 mock / 正常解析 / 调用失败降级 /
  JSON 容错 / 专业归一化与幻觉过滤。
- A-13 融合：确定性优先仲裁（补全空专业、绝不覆盖）/ 低置信度进审校队列 /
  跨图候选 / 开关关闭恒等（零差异回归）。
"""
from types import SimpleNamespace

import pytest

from services import vlm_semantics
from services.vlm_semantics import (
    LOW_CONFIDENCE_REVIEW_MAX,
    DrawingSemanticResult,
    VlmValue,
    apply_vlm_discipline,
    collect_scene_vlm,
    extract_drawing_semantics,
    merge_vlm_into_semantic_payload,
    vlm_cross_link_candidates,
    _parse_vlm_json,
)


# ── 测试替身 ──────────────────────────────────────────────────

class _FakeRouter:
    """记录调用的假 ModelRouter；可返回内容或抛错。"""

    def __init__(self, content: str | None = None, error: Exception | None = None):
        self.calls: list[tuple[str, list]] = []
        self._content = content
        self._error = error

    async def route(self, engine_name, messages, task_type="primary"):
        self.calls.append((engine_name, messages))
        if self._error is not None:
            raise self._error
        return SimpleNamespace(content=self._content)


_SAMPLE_JSON = """```json
{
  "title": {"value": "三层结构平面图", "confidence": 0.9},
  "discipline": {"value": "结构", "confidence": 0.88},
  "title_block": {
    "drawing_no": {"value": "JG-3F-01", "confidence": 0.92},
    "scale": {"value": "1:100", "confidence": 0.8},
    "date": {"value": "2024-05", "confidence": 0.7}
  },
  "cross_hints": [{"value": "与 JG-3F-02 配筋对应", "confidence": 0.6}]
}
```"""


def _enable(monkeypatch, enabled: bool = True) -> None:
    monkeypatch.setattr(vlm_semantics.settings, "vlm_semantic_enabled", enabled)


def _patch_preprocess(monkeypatch) -> None:
    monkeypatch.setattr(
        vlm_semantics,
        "preprocess_for_vlm",
        lambda data, ext: {"title_block_png": b"PNG", "overview_png": b"", "tiles": None},
    )


# ── A-11 抽取 ─────────────────────────────────────────────────

async def test_extract_disabled_does_not_call_router(monkeypatch):
    # Arrange
    _enable(monkeypatch, False)
    router = _FakeRouter(content=_SAMPLE_JSON)

    # Act
    result = await extract_drawing_semantics(
        {"id": "d1", "title": "结构平面图"}, image_bytes=b"raw", ext="pdf", router=router
    )

    # Assert
    assert result.source == "disabled"
    assert result.is_empty
    assert router.calls == []


async def test_extract_without_router_uses_mock(monkeypatch):
    _enable(monkeypatch)

    result = await extract_drawing_semantics(
        {"id": "d1", "title": "三层结构平面图"}, image_bytes=b"raw", ext="pdf", router=None
    )

    assert result.source == "mock"
    assert result.discipline is not None
    assert result.discipline.value == "structure"
    assert result.title is not None and result.title.value == "三层结构平面图"


async def test_extract_parses_vlm_json(monkeypatch):
    # Arrange
    _enable(monkeypatch)
    _patch_preprocess(monkeypatch)
    router = _FakeRouter(content=_SAMPLE_JSON)

    # Act
    result = await extract_drawing_semantics(
        {"id": "d1", "title": "x"}, image_bytes=b"raw", ext="pdf", router=router
    )

    # Assert
    assert result.source == "vlm"
    assert result.title.value == "三层结构平面图"
    assert result.discipline.value == "structure"  # 归一化「结构」→ structure
    assert result.drawing_no.value == "JG-3F-01"
    assert result.scale.value == "1:100"
    assert result.date.value == "2024-05"
    assert len(result.cross_hints) == 1
    assert result.title_block_fields["drawing_no"]["confidence"] == 0.92
    # 送模型的消息含 base64 图像块（优先 base64，vision.py 规范格式）
    engine, messages = router.calls[0]
    assert engine == vlm_semantics.VLM_ENGINE_NAME
    blocks = messages[0]["content"]
    assert any(b.get("type") == "image" and b["source"]["type"] == "base64" for b in blocks)


async def test_extract_router_error_degrades(monkeypatch):
    _enable(monkeypatch)
    _patch_preprocess(monkeypatch)
    router = _FakeRouter(error=RuntimeError("模型 500"))

    result = await extract_drawing_semantics(
        {"id": "d1", "title": "x"}, image_bytes=b"raw", ext="pdf", router=router
    )

    assert result.source == "error"
    assert result.is_empty


async def test_extract_no_image_falls_back_to_mock(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        vlm_semantics,
        "preprocess_for_vlm",
        lambda data, ext: {"title_block_png": b"", "overview_png": b"", "tiles": None},
    )
    router = _FakeRouter(content=_SAMPLE_JSON)

    result = await extract_drawing_semantics(
        {"id": "d1", "title": "建筑平面图"}, image_bytes=b"raw", ext="pdf", router=router
    )

    assert result.source == "mock"
    assert router.calls == []


async def test_extract_rejects_hallucinated_discipline(monkeypatch):
    _enable(monkeypatch)
    _patch_preprocess(monkeypatch)
    bad = '{"discipline": {"value": "厨房", "confidence": 0.9}}'
    router = _FakeRouter(content=bad)

    result = await extract_drawing_semantics(
        {"id": "d1"}, image_bytes=b"raw", ext="pdf", router=router
    )

    assert result.discipline is None  # 非法专业丢弃，防幻觉


def test_parse_vlm_json_robust():
    assert _parse_vlm_json("") == {}
    assert _parse_vlm_json("完全不是 JSON") == {}
    assert _parse_vlm_json('前缀 {"a": 1} 后缀')["a"] == 1
    assert _parse_vlm_json("```json\n{\"b\": 2}\n```")["b"] == 2
    assert _parse_vlm_json("[1,2,3]") == {}  # 非 dict 顶层丢弃


def test_parse_vlm_json_missing_fields_tolerated():
    result = vlm_semantics._result_from_parsed("d1", {"title": {"value": "T"}}, source="vlm")
    assert result.title.value == "T"
    assert result.title.confidence == 0.0  # 缺 confidence → 0
    assert result.discipline is None
    assert result.scale is None


# ── A-13 融合：apply_vlm_discipline（确定性优先）───────────────

def test_apply_vlm_discipline_fills_empty():
    drawings = [{"id": "d1", "discipline": ""}]
    vlm = {"d1": DrawingSemanticResult(
        drawing_id="d1", discipline=VlmValue("structure", 0.9))}

    out = apply_vlm_discipline(drawings, vlm)

    assert out[0]["discipline"] == "structure"
    assert out[0]["discipline_source"] == "vlm"
    assert out[0]["discipline_confidence"] == 0.9
    # 不可变：原对象未被修改
    assert drawings[0]["discipline"] == ""


def test_apply_vlm_discipline_never_overwrites_deterministic():
    drawings = [{"id": "d1", "discipline": "architecture"}]
    vlm = {"d1": DrawingSemanticResult(
        drawing_id="d1", discipline=VlmValue("structure", 0.99))}

    out = apply_vlm_discipline(drawings, vlm)

    # 冲突：确定性优先，VLM 仅作候选标注
    assert out[0]["discipline"] == "architecture"
    assert out[0]["vlm_discipline_hint"]["value"] == "structure"
    assert out[0]["vlm_discipline_hint"]["source"] == "vlm"


def test_apply_vlm_discipline_low_confidence_not_filled():
    drawings = [{"id": "d1", "discipline": ""}]
    vlm = {"d1": DrawingSemanticResult(
        drawing_id="d1", discipline=VlmValue("mep", 0.3))}

    out = apply_vlm_discipline(drawings, vlm)

    assert out[0].get("discipline") == ""
    assert "discipline_source" not in out[0]


def test_apply_vlm_discipline_empty_is_identity():
    drawings = [{"id": "d1", "discipline": "structure"}]

    out = apply_vlm_discipline(drawings, {})

    assert out is drawings  # 同一对象 → 零差异回归


# ── A-13 融合：merge_vlm_into_semantic_payload ────────────────

def _base_payload() -> dict:
    return {
        "semantic_tree": {"nodes": [], "version": 1},
        "unassigned_drawings": [],
        "semantic_version": 1,
    }


def test_merge_payload_adds_candidates_and_review_items():
    payload = _base_payload()
    vlm = {
        "d1": DrawingSemanticResult(
            drawing_id="d1",
            title=VlmValue("低置信图名", 0.4),
            discipline=VlmValue("structure", 0.5),
        )
    }

    merged = merge_vlm_into_semantic_payload(payload, vlm)

    assert len(merged["vlm_candidates"]) == 1
    # 低置信度（< 0.6）进入审校队列
    review = merged["unassigned_drawings"]
    assert any(item.get("reason") == "vlm_low_confidence" for item in review)
    # 确定性 semantic_tree 未被污染
    assert merged["semantic_tree"] == payload["semantic_tree"]


def test_merge_payload_high_confidence_not_in_review():
    payload = _base_payload()
    vlm = {"d1": DrawingSemanticResult(
        drawing_id="d1", discipline=VlmValue("structure", 0.95))}

    merged = merge_vlm_into_semantic_payload(payload, vlm)

    assert merged["unassigned_drawings"] == []
    assert LOW_CONFIDENCE_REVIEW_MAX == 0.60


def test_merge_payload_empty_is_identity():
    payload = _base_payload()

    merged = merge_vlm_into_semantic_payload(payload, {})

    assert merged is payload  # 零差异回归


def test_merge_payload_dedupes_existing_unassigned():
    payload = _base_payload()
    payload["unassigned_drawings"] = [{"drawing_id": "d1", "reason": "semantic_unassigned"}]
    vlm = {"d1": DrawingSemanticResult(
        drawing_id="d1", discipline=VlmValue("structure", 0.3))}

    merged = merge_vlm_into_semantic_payload(payload, vlm)

    d1_items = [i for i in merged["unassigned_drawings"] if i["drawing_id"] == "d1"]
    assert len(d1_items) == 1  # 已在队列 → 不重复加入


# ── A-13 融合：vlm_cross_link_candidates ──────────────────────

def test_vlm_cross_link_candidates():
    vlm = {"d1": DrawingSemanticResult(
        drawing_id="d1", cross_hints=(VlmValue("与 JG-3F-02 对应", 0.6),))}

    links = vlm_cross_link_candidates(vlm, {}, {})

    assert len(links) == 1
    assert links[0]["kind"] == "VLM提示"
    assert links[0]["source"] == "vlm"
    assert links[0]["drawing_ids"] == ["d1"]


def test_vlm_cross_link_candidates_empty_is_empty():
    assert vlm_cross_link_candidates({}, {}, {}) == []


# ── A-13 门控：collect_scene_vlm 开关关闭零副作用 ─────────────

async def test_collect_scene_vlm_disabled_returns_empty(monkeypatch):
    _enable(monkeypatch, False)
    # db 传 None：若 collect 触碰 db/router 会抛错；返回 {} 证明门控生效
    result = await collect_scene_vlm(None, [{"id": "d1", "file_key": "x.pdf"}])
    assert result == {}


async def test_collect_scene_vlm_no_drawings_returns_empty(monkeypatch):
    _enable(monkeypatch)
    assert await collect_scene_vlm(None, []) == {}
