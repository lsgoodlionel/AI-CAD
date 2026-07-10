"""图层约定加载器 + 分类器测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.model3d import layer_conventions as lc


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前后清空 lru_cache，避免降级测试污染其它用例。"""
    lc.load_conventions.cache_clear()
    yield
    lc.load_conventions.cache_clear()


# ── 加载器 ───────────────────────────────────────────────────
def test_load_conventions_parses_real_yaml():
    conv = lc.load_conventions()
    assert conv.kind_rules, "应从真实 yaml 加载出构件规则"
    assert conv.system_rules, "应从真实 yaml 加载出机电系统规则"
    kinds = {rule.kind for rule in conv.kind_rules}
    assert {"column", "beam", "slab", "wall", "door", "window", "pipe", "equipment", "axis"} <= kinds


def test_load_conventions_is_cached():
    assert lc.load_conventions() is lc.load_conventions()


# ── 精确别名（大小写不敏感）───────────────────────────────────
@pytest.mark.parametrize(
    "layer,expected",
    [
        ("S-COLU", "column"),
        ("结构柱", "column"),
        ("S-BEAM", "beam"),
        ("S-SLAB", "slab"),
        ("楼板", "slab"),
        ("S-WALL", "wall"),
        ("剪力墙", "wall"),
        ("A-WALL", "wall"),
        ("AXIS", "axis"),
        ("轴线", "axis"),
        ("DOTE", "axis"),
    ],
)
def test_classify_by_exact_alias(layer, expected):
    assert lc.classify_by_layer(layer) == expected


def test_alias_is_case_insensitive():
    assert lc.classify_by_layer("s-colu") == "column"
    assert lc.classify_by_layer("s-WaLl") == "wall"


# ── 前缀匹配 ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "layer,expected",
    [
        ("S-COLU-DIMS", "column"),
        ("S-BEAM-TEXT", "beam"),
        ("S-SLAB-PATT", "slab"),
        ("A-WALL-FULL", "wall"),
        ("S-GRID-IDEN", "axis"),
        ("M-DUCT-SUPPLY", "pipe"),
        ("E-POWR-CABLE", "equipment"),
    ],
)
def test_classify_by_prefix(layer, expected):
    assert lc.classify_by_layer(layer) == expected


# ── 子串 / 正则匹配 ──────────────────────────────────────────
@pytest.mark.parametrize(
    "layer,expected",
    [
        ("结构-框架柱-配筋", "column"),
        ("二层梁配筋图", "beam"),
        ("砌体填充墙", "wall"),
        ("给水管道平面", "pipe"),
        ("定位轴线网", "axis"),
    ],
)
def test_classify_by_substring(layer, expected):
    assert lc.classify_by_layer(layer) == expected


def test_classify_by_regex_pattern():
    # 走正则分支：STR_COL 前缀非精确别名、非 prefixes 列表，但命中 ^(?:S|STR)[-_]?COL
    assert lc.classify_by_layer("STR_COLUMN_MARK") == "column"


# ── 块名优先于图层（门窗典型场景）────────────────────────────
def test_block_name_classifies_door():
    # 图层名无法识别，靠块名 M- 前缀判定为门
    assert lc.classify_by_layer("0", block="M-1521") == "door"


def test_block_name_classifies_window():
    assert lc.classify_by_layer("A-ANNO", block="C-1815") == "window"


def test_block_substring_door_window():
    assert lc.classify_by_layer("", block="双扇门") == "door"
    assert lc.classify_by_layer("", block="推拉窗") == "window"


def test_exact_alias_layer_beats_block():
    # layer 精确别名（column）优先于 block（door）
    assert lc.classify_by_layer("S-COLU", block="M-1521") == "column"


# ── 机电系统判定 ─────────────────────────────────────────────
@pytest.mark.parametrize(
    "layer,expected_system",
    [
        ("M-DUCT-SUPPLY", "暖通"),
        ("暖通风管平面", "暖通"),
        ("P-SANR-PIPE", "给排水"),
        ("给排水系统图", "给排水"),
        ("E-POWR-CABLE", "电气"),
        ("桥架布置", "电气"),
        ("消防喷淋管", "消防"),
    ],
)
def test_classify_system(layer, expected_system):
    assert lc.classify_system(layer) == expected_system


def test_classify_system_substring_beats_prefix():
    # 图层同时含消防子串与 P- 前缀，子串（消防）应胜出
    assert lc.classify_system("P-消火栓") == "消防"


def test_classify_system_unknown_returns_none():
    assert lc.classify_system("S-COLU") is None


# ── 未知 / 空输入 ────────────────────────────────────────────
def test_unknown_layer_returns_none():
    assert lc.classify_by_layer("RANDOM-XYZ-123") is None
    assert lc.classify_by_layer("图框") is None


def test_empty_and_none_inputs_are_safe():
    assert lc.classify_by_layer("") is None
    assert lc.classify_by_layer(None) is None
    assert lc.classify_by_layer(None, block="") is None
    assert lc.classify_system("") is None
    assert lc.classify_system(None) is None


# ── 降级：yaml 缺失 / 损坏 / 无 pyyaml ───────────────────────
def test_missing_yaml_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(lc, "_CONVENTIONS_FILE", Path("/nonexistent/no.yaml"))
    lc.load_conventions.cache_clear()
    conv = lc.load_conventions()
    assert conv.kind_rules == ()
    assert conv.system_rules == ()
    assert lc.classify_by_layer("S-COLU") is None
    assert lc.classify_system("M-DUCT-X") is None


def test_no_pyyaml_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(lc, "_HAS_YAML", False)
    lc.load_conventions.cache_clear()
    assert lc.load_conventions().kind_rules == ()
    assert lc.classify_by_layer("结构柱") is None


def test_corrupt_yaml_degrades_to_empty(monkeypatch, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("conventions: [unterminated\n", encoding="utf-8")
    monkeypatch.setattr(lc, "_CONVENTIONS_FILE", bad)
    lc.load_conventions.cache_clear()
    assert lc.load_conventions().kind_rules == ()


def test_non_mapping_root_degrades_to_empty(monkeypatch, tmp_path):
    bad = tmp_path / "list.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    monkeypatch.setattr(lc, "_CONVENTIONS_FILE", bad)
    lc.load_conventions.cache_clear()
    assert lc.load_conventions().kind_rules == ()
