"""Phase H5 职责 A 单测 —— BOM 抽取 / 数量对齐。纯函数 + mock router。"""
import pytest

from services.component_bom import (
    build_bom_messages,
    extract_bom,
    parse_bom,
    reconcile_from_counts,
)


def test_reconcile_diff():
    by_type = {"column": 45, "wall": 10}
    bom = {"column": 48, "wall": 10, "beam": 20}
    r = reconcile_from_counts(by_type, bom)
    assert r["column"] == {"expected": 48, "actual": 45, "diff": 3}    # 漏 3
    assert r["wall"]["diff"] == 0
    assert r["beam"] == {"expected": 20, "actual": 0, "diff": 20}


def test_reconcile_over_detection_negative_diff():
    r = reconcile_from_counts({"pile": 55}, {"pile": 48})
    assert r["pile"]["diff"] == -7


def test_build_bom_messages_includes_schedule():
    msgs = build_bom_messages("KZ1 柱 48根")
    assert msgs[0]["role"] == "system"
    assert "KZ1 柱 48根" in msgs[1]["content"]


def test_parse_bom_filters_invalid_types_and_values():
    v = parse_bom('{"column": 48, "外星构件": 9, "pile": "not-int", "wall": 10}')
    assert v["available"] is True
    assert v["bom"] == {"column": 48, "wall": 10}   # 非法类型/非整数被过滤


def test_parse_bom_garbage_unavailable():
    assert parse_bom("模型无响应")["available"] is False
    assert parse_bom("")["available"] is False
    assert parse_bom('{"column": 0}')["available"] is False   # 0 无意义


class _Resp:
    def __init__(self, content): self.content = content


class _Router:
    def __init__(self, content): self._c = content
    async def route(self, engine, messages): return _Resp(self._c)


class _FailRouter:
    async def route(self, engine, messages): raise RuntimeError("down")


@pytest.mark.asyncio
async def test_extract_bom_with_mock_router():
    r = _Router('{"column": 480, "pile": 96}')
    v = await extract_bom("构件表内容", r)
    assert v["available"] is True
    assert v["bom"] == {"column": 480, "pile": 96}


@pytest.mark.asyncio
async def test_extract_bom_degrades():
    assert (await extract_bom("x", None))["available"] is False
    assert (await extract_bom("", _Router('{"column":1}')))["available"] is False   # 空表
    assert (await extract_bom("x", _FailRouter()))["available"] is False
