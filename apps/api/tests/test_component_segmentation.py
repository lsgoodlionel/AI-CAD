"""Phase H5 职责 C 单测 —— 语义分区 prompt/parse/降级。纯函数 + mock router。"""
import pytest

from services.component_segmentation import (
    build_segmentation_messages,
    parse_segmentation,
    segment_regions,
)


def test_build_messages_has_text_and_image():
    msgs = build_segmentation_messages([b"\x89PNG-fake"])
    content = msgs[0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image"
    assert content[1]["source"]["type"] == "base64"


def test_parse_valid_regions():
    v = parse_segmentation(
        '{"regions":[{"type":"column","bbox":[0.1,0.2,0.05,0.05],"confidence":0.8},'
        '{"type":"equipment","bbox":[0.5,0.5,0.2,0.1],"confidence":0.6}]}')
    assert v["available"] is True
    assert len(v["regions"]) == 2
    assert v["regions"][0]["type"] == "column"


def test_parse_filters_invalid_type_and_bbox():
    v = parse_segmentation(
        '{"regions":[{"type":"外星","bbox":[0.1,0.1,0.1,0.1]},'      # 非法类型
        '{"type":"wall","bbox":[0.1,0.1,0.1]},'                      # bbox 长度错
        '{"type":"wall","bbox":[0.1,0.1,-0.2,0.1]},'                 # 宽负
        '{"type":"pile","bbox":[0.2,0.2,0.05,0.05],"confidence":0.9}]}')
    assert len(v["regions"]) == 1
    assert v["regions"][0]["type"] == "pile"


def test_parse_garbage_unavailable():
    assert parse_segmentation("无响应")["available"] is False
    assert parse_segmentation('{"regions":"notlist"}')["available"] is False
    assert parse_segmentation('{"regions":[]}')["available"] is False


def test_parse_confidence_defaults_when_missing():
    v = parse_segmentation('{"regions":[{"type":"slab","bbox":[0,0,0.3,0.3]}]}')
    assert v["regions"][0]["confidence"] == 0.5


class _Resp:
    def __init__(self, c): self.content = c


class _Router:
    def __init__(self, c): self._c = c
    async def route(self, engine, messages): return _Resp(self._c)


class _FailRouter:
    async def route(self, engine, messages): raise RuntimeError("down")


@pytest.mark.asyncio
async def test_segment_with_mock_router():
    r = _Router('{"regions":[{"type":"column","bbox":[0.1,0.1,0.05,0.05],"confidence":0.7}]}')
    v = await segment_regions([b"png"], r)
    assert v["available"] is True
    assert v["regions"][0]["type"] == "column"


@pytest.mark.asyncio
async def test_segment_degrades():
    assert (await segment_regions([b"png"], None))["available"] is False
    assert (await segment_regions([], _Router("{}")))["available"] is False   # 无图
    assert (await segment_regions([b"png"], _FailRouter()))["available"] is False
