"""DWG → DXF 转换支持（dwg_support.ensure_dxf）测试"""
import pytest

from core.ai_review import dwg_support
from core.ai_review.dwg_support import ODA_MISSING_WARNING, ensure_dxf


DWG_BYTES = b"AC1032" + b"\x00" * 32   # 带 DWG 魔数的二进制内容
TEXT_BYTES = b"0\nSECTION\n2\nHEADER\n"  # DXF 文本内容


@pytest.mark.unit
def test_non_dwg_passthrough():
    data, ext, warning = ensure_dxf(TEXT_BYTES, "pdf")
    assert (data, ext, warning) == (TEXT_BYTES, "pdf", None)


@pytest.mark.unit
def test_dwg_without_magic_treated_as_dxf():
    """DXF 文本误存为 .dwg → 按 dxf 处理，不告警"""
    data, ext, warning = ensure_dxf(TEXT_BYTES, "dwg")
    assert data == TEXT_BYTES
    assert ext == "dxf"
    assert warning is None


@pytest.mark.unit
def test_dwg_without_oda_config_degrades(monkeypatch):
    """ODA 未配置且无 dwg2dxf → 降级提示（屏蔽测试机上真实安装的 dwg2dxf）"""
    monkeypatch.setattr(dwg_support.settings, "oda_converter_path", "")
    monkeypatch.setattr(dwg_support.shutil, "which", lambda _n: None)
    data, ext, warning = ensure_dxf(DWG_BYTES, "dwg")
    assert data == DWG_BYTES
    assert ext == "dwg"
    assert warning == ODA_MISSING_WARNING


@pytest.mark.unit
def test_dwg_converted_when_oda_available(monkeypatch):
    monkeypatch.setattr(dwg_support.settings, "oda_converter_path", "/opt/oda/converter")
    monkeypatch.setattr(dwg_support, "_convert_with_oda", lambda _data: b"CONVERTED_DXF")
    data, ext, warning = ensure_dxf(DWG_BYTES, "dwg")
    assert data == b"CONVERTED_DXF"
    assert ext == "dxf"
    assert warning is None


@pytest.mark.unit
def test_dwg_conversion_failure_degrades_with_warning(monkeypatch):
    monkeypatch.setattr(dwg_support.settings, "oda_converter_path", "/opt/oda/converter")

    def _boom(_data: bytes) -> bytes:
        raise RuntimeError("converter crashed")

    monkeypatch.setattr(dwg_support, "_convert_with_oda", _boom)
    # 固定 dwg2dxf 存在且同样失败（消除 CI/本机 dwg2dxf 安装差异）
    monkeypatch.setattr(dwg_support.shutil, "which", lambda _n: "/usr/local/bin/dwg2dxf")

    def _boom2(_exe: str, _data: bytes) -> bytes:
        raise RuntimeError("dwg2dxf crashed")

    monkeypatch.setattr(dwg_support, "_convert_with_dwg2dxf", _boom2)
    data, ext, warning = ensure_dxf(DWG_BYTES, "dwg")
    assert data == DWG_BYTES
    assert ext == "dwg"
    assert warning and "转换失败" in warning


# ── GNU LibreDWG dwg2dxf 兜底链路 ────────────────────────────────

@pytest.mark.unit
def test_dwg_falls_back_to_dwg2dxf_when_oda_missing(monkeypatch):
    """ODA 未配置但 PATH 有 dwg2dxf → 走 LibreDWG 转换成功"""
    monkeypatch.setattr(dwg_support.settings, "oda_converter_path", "")
    monkeypatch.setattr(dwg_support.shutil, "which", lambda _n: "/usr/local/bin/dwg2dxf")
    monkeypatch.setattr(
        dwg_support, "_convert_with_dwg2dxf", lambda _exe, _d: b"LIBREDWG_DXF"
    )
    data, ext, warning = ensure_dxf(DWG_BYTES, "dwg")
    assert (data, ext, warning) == (b"LIBREDWG_DXF", "dxf", None)


@pytest.mark.unit
def test_dwg_oda_failure_then_dwg2dxf_success(monkeypatch):
    """ODA 配置了但转换失败 → 自动降级 dwg2dxf 成功"""
    monkeypatch.setattr(dwg_support.settings, "oda_converter_path", "/opt/oda/AppRun")

    def _oda_boom(_data: bytes) -> bytes:
        raise RuntimeError("oda crashed")

    monkeypatch.setattr(dwg_support, "_convert_with_oda", _oda_boom)
    monkeypatch.setattr(dwg_support.shutil, "which", lambda _n: "/usr/local/bin/dwg2dxf")
    monkeypatch.setattr(
        dwg_support, "_convert_with_dwg2dxf", lambda _exe, _d: b"LIBREDWG_DXF"
    )
    data, ext, warning = ensure_dxf(DWG_BYTES, "dwg")
    assert (data, ext, warning) == (b"LIBREDWG_DXF", "dxf", None)


@pytest.mark.unit
def test_dwg_no_converter_at_all_degrades(monkeypatch):
    monkeypatch.setattr(dwg_support.settings, "oda_converter_path", "")
    monkeypatch.setattr(dwg_support.shutil, "which", lambda _n: None)
    data, ext, warning = ensure_dxf(DWG_BYTES, "dwg")
    assert ext == "dwg"
    assert warning == ODA_MISSING_WARNING
