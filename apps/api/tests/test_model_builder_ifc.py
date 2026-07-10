"""A-03 程序化 IFC 建模分支测试：灰度门控 + 降级路径（mock 掉 MinIO/Node/ifcopenshell）。"""
import asyncio

import pytest

from services import model_builder


def _run(coro):
    return asyncio.run(coro)


def test_disabled_flag_returns_none(monkeypatch):
    """开关关闭时不建模，行为回退现网。"""
    monkeypatch.setattr(model_builder.settings, "model_ifc_enabled", False)
    result = _run(
        model_builder._maybe_build_programmatic_ifc(
            "1", "P", [{"building_key": "b"}], [], "elements"
        )
    )
    assert result is None


def test_texture_mode_returns_none(monkeypatch):
    """无确定性构件（纯贴图）时不建模。"""
    monkeypatch.setattr(model_builder.settings, "model_ifc_enabled", True)
    result = _run(
        model_builder._maybe_build_programmatic_ifc(
            "1", "P", [{"building_key": "b"}], [], "texture"
        )
    )
    assert result is None


def test_no_buildings_returns_none(monkeypatch):
    monkeypatch.setattr(model_builder.settings, "model_ifc_enabled", True)
    result = _run(
        model_builder._maybe_build_programmatic_ifc("1", "P", [], [], "elements")
    )
    assert result is None


def test_enabled_builds_model_ifc(monkeypatch):
    """开关开启 + 有构件：产出 model_ifc 契约，standard 字段齐全。"""
    import services.ifc_mapping as ifc_mapping

    monkeypatch.setattr(model_builder.settings, "model_ifc_enabled", True)
    monkeypatch.setattr(model_builder, "upload_file", lambda *a, **k: a[1])
    monkeypatch.setattr(ifc_mapping, "build_ifc_from_scene", lambda scene, name=None: b"IFC")
    monkeypatch.setattr(
        model_builder, "_convert_fragments_quiet",
        lambda b, p: "projects/1/model_ifc/project.frag",
    )
    result = _run(
        model_builder._maybe_build_programmatic_ifc(
            "1", "P", [{"building_key": "b"}], [], "elements"
        )
    )
    assert result["build_mode"] == "ifc"
    assert result["is_estimated"] is True
    assert result["ifc_key"] == "projects/1/model_ifc/project.ifc"
    assert result["frag_key"] == "projects/1/model_ifc/project.frag"
    assert "generated_at" in result


def test_fragments_failure_keeps_ifc(monkeypatch):
    """Fragments 转换失败：frag_key 降级为 None，但 IFC 仍产出（不中断）。"""
    import services.fragments_convert as fragments_convert
    import services.ifc_mapping as ifc_mapping

    monkeypatch.setattr(model_builder.settings, "model_ifc_enabled", True)
    monkeypatch.setattr(model_builder, "upload_file", lambda *a, **k: a[1])
    monkeypatch.setattr(ifc_mapping, "build_ifc_from_scene", lambda scene, name=None: b"IFC")

    def _boom(*a, **k):
        raise fragments_convert.FragmentsConversionError("node 缺失")

    monkeypatch.setattr(fragments_convert, "convert_and_upload_ifc_bytes", _boom)
    result = _run(
        model_builder._maybe_build_programmatic_ifc(
            "1", "P", [{"building_key": "b"}], [], "elements"
        )
    )
    assert result["frag_key"] is None
    assert result["build_mode"] == "ifc"


def test_ifc_build_failure_degrades(monkeypatch):
    """IFC 建模本身抛错：整体降级返回 None，绝不中断 build_scene。"""
    import services.ifc_mapping as ifc_mapping

    monkeypatch.setattr(model_builder.settings, "model_ifc_enabled", True)

    def _boom(*a, **k):
        raise RuntimeError("ifcopenshell 崩了")

    monkeypatch.setattr(ifc_mapping, "build_ifc_from_scene", _boom)
    result = _run(
        model_builder._maybe_build_programmatic_ifc(
            "1", "P", [{"building_key": "b"}], [], "elements"
        )
    )
    assert result is None
