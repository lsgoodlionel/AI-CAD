"""Phase H5 大模型复核单测 —— prompt 构建 / 判据解析 / 降级。纯函数 + mock router。"""
import pytest

from services.component_llm_review import (
    build_review_messages,
    parse_review_verdict,
    review_component,
)


def test_build_messages_includes_context():
    inst = {"type": "column", "grid_ref": "C-3", "type_label": "钢立柱",
            "engines": ["rule"], "source_drawings": ["S-1-20-005C"],
            "obs_count": 2, "confidence": 0.3}
    msgs = build_review_messages(inst)
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "钢立柱" in msgs[1]["content"]
    assert "S-1-20-005C" in msgs[1]["content"]


def test_parse_confirm():
    v = parse_review_verdict('{"verdict":"confirm","suggested_type":null,"reason":"柱正确"}')
    assert v["available"] is True
    assert v["verdict"] == "confirm"
    assert v["suggested_type"] is None
    assert v["reason"] == "柱正确"


def test_parse_reclass_with_type():
    v = parse_review_verdict('前言\n```json\n{"verdict":"reclass","suggested_type":"pile","reason":"应为桩"}\n```')
    assert v["verdict"] == "reclass"
    assert v["suggested_type"] == "pile"


def test_parse_reject():
    v = parse_review_verdict('{"verdict":"reject","reason":"误检"}')
    assert v["verdict"] == "reject"
    assert v["suggested_type"] is None


def test_parse_reclass_without_valid_type_is_unavailable():
    """改类却没给合法新类型 → 不作建议(不误导人审)。"""
    v = parse_review_verdict('{"verdict":"reclass","suggested_type":"外星构件"}')
    assert v["available"] is False


def test_parse_garbage_is_unavailable():
    assert parse_review_verdict("模型无响应").get("available") is False
    assert parse_review_verdict("").get("available") is False
    assert parse_review_verdict('{"verdict":"maybe"}').get("available") is False


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeRouter:
    def __init__(self, content):
        self._content = content

    async def route(self, engine, messages):
        return _FakeResponse(self._content)


class _FailRouter:
    async def route(self, engine, messages):
        raise RuntimeError("LLM down")


@pytest.mark.asyncio
async def test_review_component_with_mock_router():
    router = _FakeRouter('{"verdict":"reclass","suggested_type":"pile","reason":"圆形截面近似桩"}')
    v = await review_component({"type": "column", "grid_ref": "C-3"}, router)
    assert v["available"] is True
    assert v["verdict"] == "reclass"
    assert v["suggested_type"] == "pile"


@pytest.mark.asyncio
async def test_review_component_degrades_on_none_or_failure():
    assert (await review_component({"type": "column"}, None))["available"] is False
    assert (await review_component({"type": "column"}, _FailRouter()))["available"] is False
