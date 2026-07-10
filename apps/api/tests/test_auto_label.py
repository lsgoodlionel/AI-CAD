"""C-04 自动标注引擎单元测试（TDD）。

覆盖：基础分类器主判据、置信度分级、label_source 定位、补充映射表回退、
机电系统判定、未知图层降级 None、空文档不抛异常、质量报告统计、
补充映射表加载（无效正则跳过 / 缺失文件降级 / 越界 taxonomy 跳过）。
"""
from __future__ import annotations

from pathlib import Path

from core.model3d.dataset.auto_label import (
    CONF_ALIAS,
    CONF_BLOCK,
    CONF_LAYER,
    CONF_MAP,
    AutoLabelResult,
    LabeledPrimitive,
    LayerClassMap,
    auto_label,
    load_layer_class_map,
    weak_label_report,
)
from core.model3d.preprocess.schema import Primitive, PrimitiveDoc


# ── 构造辅助 ─────────────────────────────────────────────────────
def _prim(pid: int, layer: str = "", block: str = "", ptype: str = "line") -> Primitive:
    pts = ((0.0, 0.0), (1.0, 1.0))
    return Primitive(id=pid, type=ptype, points=pts, layer=layer, block=block)


def _doc(*prims: Primitive) -> PrimitiveDoc:
    return PrimitiveDoc(primitives=tuple(prims))


def _by_id(result: AutoLabelResult) -> dict[int, LabeledPrimitive]:
    return {lp.primitive_id: lp for lp in result.labeled}


# ── 基础分类器主判据 + 置信度分级 ────────────────────────────────
def test_exact_layer_alias_is_high_confidence_layer_source() -> None:
    # Arrange: "S-BEAM" 是 layer_conventions 中 beam 的精确别名
    result = auto_label(_doc(_prim(1, layer="S-BEAM")))

    # Act
    lp = _by_id(result)[1]

    # Assert
    assert lp.category == "beam"
    assert lp.label_source == "layer"
    assert lp.confidence == CONF_ALIAS


def test_layer_prefix_is_medium_confidence() -> None:
    # "S-BEAM-2F" 非精确别名，但命中前缀/正则 → 中置信度
    result = auto_label(_doc(_prim(1, layer="S-BEAM-2F")))
    lp = _by_id(result)[1]
    assert lp.category == "beam"
    assert lp.label_source == "layer"
    assert lp.confidence == CONF_LAYER


def test_block_name_match_is_low_confidence_block_source() -> None:
    # 块名前缀 "MEN-" → door；图层为通用层不参与判定
    result = auto_label(_doc(_prim(1, layer="0", block="MEN-1021")))
    lp = _by_id(result)[1]
    assert lp.category == "door"
    assert lp.label_source == "block"
    assert lp.confidence == CONF_BLOCK


def test_chinese_layer_substring_matches_column() -> None:
    result = auto_label(_doc(_prim(1, layer="结构-框架柱")))
    lp = _by_id(result)[1]
    assert lp.category == "column"
    assert lp.confidence in (CONF_ALIAS, CONF_LAYER)


# ── 机电系统判定 ─────────────────────────────────────────────────
def test_mep_system_detected_alongside_category() -> None:
    result = auto_label(_doc(_prim(1, layer="P-PIPE-给水")))
    lp = _by_id(result)[1]
    assert lp.category == "pipe"
    assert lp.mep_system == "给排水"


def test_mep_system_none_for_pure_structure() -> None:
    result = auto_label(_doc(_prim(1, layer="S-BEAM")))
    lp = _by_id(result)[1]
    assert lp.mep_system is None


# ── 未知图层降级 None ────────────────────────────────────────────
def test_unknown_layer_degrades_to_none() -> None:
    result = auto_label(_doc(_prim(1, layer="RANDOM-XYZ-123", block="")))
    lp = _by_id(result)[1]
    assert lp.category is None
    assert lp.confidence is None
    assert lp.label_source == "none"
    assert lp.mep_system is None


def test_empty_layer_and_block_is_none() -> None:
    result = auto_label(_doc(_prim(1, layer="", block="")))
    lp = _by_id(result)[1]
    assert lp.category is None
    assert lp.label_source == "none"


# ── 补充映射表回退 ───────────────────────────────────────────────
def _write_map(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "map.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_extra_map_fallback_when_base_misses(tmp_path: Path) -> None:
    # 基础分类器无法识别的院自定义前缀，由补充映射兜底
    body = (
        "version: '1.0'\n"
        "conventions:\n"
        "  - kind: slab\n"
        "    prefixes: ['JG-LB']\n"
    )
    extra = load_layer_class_map(_write_map(tmp_path, body))
    result = auto_label(_doc(_prim(1, layer="JG-LB-3F")), extra_map=extra)
    lp = _by_id(result)[1]
    assert lp.category == "slab"
    assert lp.label_source == "layer_class_map"
    assert lp.confidence == CONF_MAP


def test_base_classifier_takes_priority_over_extra_map(tmp_path: Path) -> None:
    # 即便补充映射把 S-BEAM 映射成 wall，基础分类器（beam）仍为主判据
    body = (
        "version: '1.0'\n"
        "conventions:\n"
        "  - kind: wall\n"
        "    aliases: ['S-BEAM']\n"
    )
    extra = load_layer_class_map(_write_map(tmp_path, body))
    result = auto_label(_doc(_prim(1, layer="S-BEAM")), extra_map=extra)
    assert _by_id(result)[1].category == "beam"


def test_extra_map_system_fallback(tmp_path: Path) -> None:
    body = (
        "version: '1.0'\n"
        "systems:\n"
        "  - system: 电气\n"
        "    substrings: ['MYCUSTOMBUS']\n"
    )
    extra = load_layer_class_map(_write_map(tmp_path, body))
    result = auto_label(_doc(_prim(1, layer="ZZ-MYCUSTOMBUS-01")), extra_map=extra)
    assert _by_id(result)[1].mep_system == "电气"


# ── 补充映射表加载健壮性 ─────────────────────────────────────────
def test_invalid_regex_entry_skipped_not_raised(tmp_path: Path) -> None:
    body = (
        "version: '1.0'\n"
        "conventions:\n"
        "  - kind: beam\n"
        "    patterns: ['[unclosed', 'BEAM']\n"
    )
    # 无效正则单条跳过，不抛异常；有效条目仍生效
    mapping = load_layer_class_map(_write_map(tmp_path, body))
    assert mapping.classify("SOME-BEAM-LAYER") == "beam"


def test_out_of_taxonomy_kind_skipped(tmp_path: Path) -> None:
    body = (
        "version: '1.0'\n"
        "conventions:\n"
        "  - kind: staircase\n"      # 越界类别，必须跳过
        "    aliases: ['LT']\n"
        "  - kind: slab\n"
        "    aliases: ['MYSLAB']\n"
    )
    mapping = load_layer_class_map(_write_map(tmp_path, body))
    assert mapping.classify("LT") is None
    assert mapping.classify("MYSLAB") == "slab"


def test_out_of_taxonomy_system_skipped(tmp_path: Path) -> None:
    body = (
        "version: '1.0'\n"
        "systems:\n"
        "  - system: 燃气\n"          # 越界系统，必须跳过
        "    substrings: ['RQ']\n"
    )
    mapping = load_layer_class_map(_write_map(tmp_path, body))
    assert mapping.classify_system("RQ-LINE") is None


def test_missing_map_file_degrades_to_empty(tmp_path: Path) -> None:
    mapping = load_layer_class_map(tmp_path / "does-not-exist.yaml")
    assert mapping.category_rules == ()
    assert mapping.system_rules == ()
    assert mapping.classify("S-BEAM") is None


def test_shipped_map_loads_non_empty() -> None:
    # 默认随包 layer_class_map.yaml 应可加载且非空
    mapping = load_layer_class_map()
    assert len(mapping.category_rules) > 0
    assert len(mapping.system_rules) > 0


# ── 质量报告统计 ─────────────────────────────────────────────────
def test_weak_label_report_statistics() -> None:
    result = auto_label(_doc(
        _prim(1, layer="S-BEAM"),          # beam / layer
        _prim(2, layer="S-COLU"),          # column / layer
        _prim(3, layer="0", block="MEN-1"),  # door / block
        _prim(4, layer="RANDOM-XYZ"),      # none
        _prim(5, layer="UNKNOWN"),         # none
    ))
    report = result.report

    assert report["total"] == 5
    assert report["labeled"] == 3
    assert report["unlabeled"] == 2
    assert abs(report["coverage"] - 0.6) < 1e-9
    assert abs(report["unlabeled_ratio"] - 0.4) < 1e-9
    assert report["by_category"]["beam"] == 1
    assert report["by_category"]["column"] == 1
    assert report["by_category"]["door"] == 1
    assert report["by_category"]["wall"] == 0
    assert report["by_source"]["layer"] == 2
    assert report["by_source"]["block"] == 1
    assert report["by_source"]["none"] == 2


def test_report_empty_document_no_division_error() -> None:
    result = auto_label(_doc())
    report = result.report
    assert report["total"] == 0
    assert report["labeled"] == 0
    assert report["coverage"] == 0.0
    assert report["unlabeled_ratio"] == 0.0
    assert result.labeled == ()


def test_weak_label_report_direct_on_empty_tuple() -> None:
    report = weak_label_report(())
    assert report["total"] == 0
    assert report["coverage"] == 0.0


# ── 优雅降级：整体不抛异常 ───────────────────────────────────────
def test_auto_label_none_doc_does_not_raise() -> None:
    # 契约外的 None 也不应抛异常（跨边界稳健）
    result = auto_label(PrimitiveDoc(primitives=()))
    assert isinstance(result, AutoLabelResult)
    assert result.labeled == ()


def test_labeled_primitive_is_frozen() -> None:
    import dataclasses

    import pytest

    result = auto_label(_doc(_prim(1, layer="S-BEAM")))
    lp = result.labeled[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        lp.category = "wall"  # type: ignore[misc]


def test_primitive_type_preserved_in_label() -> None:
    result = auto_label(_doc(_prim(7, layer="S-BEAM", ptype="polyline")))
    lp = _by_id(result)[7]
    assert lp.primitive_id == 7
    assert lp.primitive_type == "polyline"
