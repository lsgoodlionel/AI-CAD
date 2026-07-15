"""剖面标高 VLM 第二源兜底测试（后续工作 item1）。

验证：仅结构专业 + 矢量/OCR 标高不足才触发 VLM；灰度开关默认关闭；单图失败/
超时不阻断整链；置信度过滤（绝不虚高）；`_recover_section_z` 按开关决定是否
调用兜底（开关关闭时零差异回归，不建 VLM 调用、不多耗时）。
"""
import sys
import types

import pytest

# 复用 test_model_builder_cross_view.py 的轻量桩，避免真实 minio / pydantic_settings 依赖
if "pydantic_settings" not in sys.modules:
    module = types.ModuleType("pydantic_settings")

    class _BaseSettings:
        def __init__(self, **kwargs):
            for key, value in self.__class__.__dict__.items():
                if key.startswith("_") or callable(value):
                    continue
                setattr(self, key, value)
            for key, value in kwargs.items():
                setattr(self, key, value)

    module.BaseSettings = _BaseSettings
    sys.modules["pydantic_settings"] = module

if "minio" not in sys.modules:
    minio_module = types.ModuleType("minio")

    class _Minio:
        def __init__(self, *args, **kwargs):
            pass

    minio_module.Minio = _Minio
    sys.modules["minio"] = minio_module
    error_module = types.ModuleType("minio.error")

    class S3Error(Exception):
        pass

    error_module.S3Error = S3Error
    sys.modules["minio.error"] = error_module

import services.model_builder as model_builder  # noqa: E402
from core.model3d.section_level_extractor import LevelMark, SectionLevels  # noqa: E402
from core.model3d.vlm_read.types import ElevationCandidate, VlmReadResult  # noqa: E402


def _structure_drawing(drawing_id: str, file_key: str = "") -> dict:
    return {
        "id": drawing_id,
        "title": "1-1剖面图",
        "drawing_no": f"A-{drawing_id}",
        "discipline": "structure",
        "file_key": file_key or f"{drawing_id}.pdf",
    }


# ── _needs_vlm_elevation：仅结构专业 + 主源不足才需要 VLM ─────────

@pytest.mark.unit
def test_needs_vlm_elevation_false_for_non_structure_discipline():
    drawing = {"id": "d1", "discipline": "architecture"}
    assert model_builder._needs_vlm_elevation(drawing, {}) is False


@pytest.mark.unit
def test_needs_vlm_elevation_true_when_structure_and_no_primary_entry():
    drawing = {"id": "d1", "discipline": "structure"}
    assert model_builder._needs_vlm_elevation(drawing, {}) is True


@pytest.mark.unit
def test_needs_vlm_elevation_true_when_structure_and_marks_below_threshold():
    levels = SectionLevels(marks=(LevelMark(0.0, "0.000", 0.9, {}),), reason=None, fit={})
    drawing = {"id": "d1", "discipline": "structure"}
    assert model_builder._needs_vlm_elevation(drawing, {"d1": levels}) is True


@pytest.mark.unit
def test_needs_vlm_elevation_false_when_structure_and_marks_sufficient():
    levels = SectionLevels(
        marks=(LevelMark(0.0, "0.000", 0.9, {}), LevelMark(3.6, "3.600", 0.9, {})),
        reason=None, fit={},
    )
    drawing = {"id": "d1", "discipline": "structure"}
    assert model_builder._needs_vlm_elevation(drawing, {"d1": levels}) is False


# ── _vlm_section_z_enabled：灰度开关，默认关闭 ────────────────────

@pytest.mark.unit
def test_vlm_section_z_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv(model_builder._VLM_SECTION_Z_ENV, raising=False)
    assert model_builder._vlm_section_z_enabled() is False


@pytest.mark.unit
def test_vlm_section_z_enabled_respects_truthy_values(monkeypatch):
    for value in ("1", "true", "True", "yes", "YES"):
        monkeypatch.setenv(model_builder._VLM_SECTION_Z_ENV, value)
        assert model_builder._vlm_section_z_enabled() is True


@pytest.mark.unit
def test_vlm_section_z_enabled_false_for_falsy_values(monkeypatch):
    for value in ("0", "false", "", "no"):
        monkeypatch.setenv(model_builder._VLM_SECTION_Z_ENV, value)
        assert model_builder._vlm_section_z_enabled() is False


# ── _vlm_section_z_fallback：端到端（mock VLM，绝不联网）──────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_vlm_fallback_only_calls_structural_insufficient_pdf(monkeypatch):
    """非结构专业剖面不调用 VLM（discipline 过滤生效）。"""
    drawings = [
        _structure_drawing("struct1"),
        {"id": "arch1", "title": "1-1剖面图", "discipline": "architecture", "file_key": "arch1.pdf"},
    ]
    calls: list[bytes] = []

    async def fake_read_drawing_vlm(image_bytes, *, timeout=None, **kwargs):
        calls.append(image_bytes)
        return VlmReadResult(
            elevations=(
                ElevationCandidate(value_m=-3.2, confidence=0.9, evidence="-3.200"),
                ElevationCandidate(value_m=15.0, confidence=0.5, evidence="+15.00"),
            ),
            backend="qwen3.5-vision",
            model="qwen3.5:latest",
        )

    monkeypatch.setattr("core.model3d.vlm_read.read_drawing_vlm", fake_read_drawing_vlm)
    monkeypatch.setattr(model_builder, "get_file_bytes", lambda file_key: b"%PDF-fake")
    monkeypatch.setattr(model_builder, "_pdf_first_page_png_sync", lambda data: b"png-bytes")

    result = await model_builder._vlm_section_z_fallback(drawings, {})

    assert len(calls) == 1  # 仅结构专业那张被调用
    assert "struct1" in result
    assert "arch1" not in result
    # 低置信度候选（0.5 < _VLM_SECTION_Z_MIN_CONFIDENCE=0.6）被过滤，绝不虚高采信
    assert len(result["struct1"]) == 1
    assert result["struct1"][0].value_m == pytest.approx(-3.2)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vlm_fallback_skips_drawing_with_sufficient_primary_marks(monkeypatch):
    """结构专业但主源标高已足够 → 不调用 VLM（性能保护，不逢图必调）。"""
    levels = SectionLevels(
        marks=(LevelMark(0.0, "0.000", 0.9, {}), LevelMark(3.6, "3.600", 0.9, {})),
        reason=None, fit={},
    )
    drawings = [_structure_drawing("struct1")]
    calls: list[bytes] = []

    async def fake_read_drawing_vlm(image_bytes, *, timeout=None, **kwargs):
        calls.append(image_bytes)
        return VlmReadResult(backend="qwen3.5-vision", model="m")

    monkeypatch.setattr("core.model3d.vlm_read.read_drawing_vlm", fake_read_drawing_vlm)
    monkeypatch.setattr(model_builder, "get_file_bytes", lambda file_key: b"%PDF-fake")
    monkeypatch.setattr(model_builder, "_pdf_first_page_png_sync", lambda data: b"png-bytes")

    result = await model_builder._vlm_section_z_fallback(drawings, {"struct1": levels})

    assert calls == []
    assert result == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vlm_fallback_caps_max_drawings(monkeypatch):
    """单次构建最多送 VLM 的剖面数受 _VLM_SECTION_Z_MAX_DRAWINGS 上限保护。"""
    n = model_builder._VLM_SECTION_Z_MAX_DRAWINGS + 3
    drawings = [_structure_drawing(f"s{i}") for i in range(n)]
    calls: list[bytes] = []

    async def fake_read_drawing_vlm(image_bytes, *, timeout=None, **kwargs):
        calls.append(image_bytes)
        return VlmReadResult(backend="qwen3.5-vision", model="m")

    monkeypatch.setattr("core.model3d.vlm_read.read_drawing_vlm", fake_read_drawing_vlm)
    monkeypatch.setattr(model_builder, "get_file_bytes", lambda file_key: b"%PDF-fake")
    monkeypatch.setattr(model_builder, "_pdf_first_page_png_sync", lambda data: b"png-bytes")

    await model_builder._vlm_section_z_fallback(drawings, {})

    assert len(calls) == model_builder._VLM_SECTION_Z_MAX_DRAWINGS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vlm_fallback_single_drawing_failure_does_not_abort_others(monkeypatch):
    """单图 VLM/渲染异常一律跳过，不阻断其余剖面（同 _section_levels_sync 降级纪律）。"""
    drawings = [_structure_drawing("bad"), _structure_drawing("good")]

    async def fake_read_drawing_vlm(image_bytes, *, timeout=None, **kwargs):
        if image_bytes == b"bad-png":
            raise RuntimeError("simulated VLM failure")
        return VlmReadResult(
            elevations=(ElevationCandidate(value_m=1.0, confidence=0.9, evidence="x"),),
            backend="qwen3.5-vision", model="m",
        )

    def fake_get_bytes(file_key: str) -> bytes:
        return b"bad-data" if file_key == "bad.pdf" else b"good-data"

    def fake_render(data: bytes) -> bytes:
        return b"bad-png" if data == b"bad-data" else b"good-png"

    monkeypatch.setattr("core.model3d.vlm_read.read_drawing_vlm", fake_read_drawing_vlm)
    monkeypatch.setattr(model_builder, "get_file_bytes", fake_get_bytes)
    monkeypatch.setattr(model_builder, "_pdf_first_page_png_sync", fake_render)

    result = await model_builder._vlm_section_z_fallback(drawings, {})

    assert "bad" not in result
    assert "good" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vlm_fallback_skips_when_pdf_render_fails(monkeypatch):
    """PDF 渲染失败（返回空字节）→ 跳过，不调用 VLM。"""
    drawings = [_structure_drawing("s1")]
    calls: list[bytes] = []

    async def fake_read_drawing_vlm(image_bytes, *, timeout=None, **kwargs):
        calls.append(image_bytes)
        return VlmReadResult(backend="qwen3.5-vision", model="m")

    monkeypatch.setattr("core.model3d.vlm_read.read_drawing_vlm", fake_read_drawing_vlm)
    monkeypatch.setattr(model_builder, "get_file_bytes", lambda file_key: b"data")
    monkeypatch.setattr(model_builder, "_pdf_first_page_png_sync", lambda data: b"")

    result = await model_builder._vlm_section_z_fallback(drawings, {})

    assert calls == []
    assert result == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vlm_fallback_skips_non_pdf_drawings(monkeypatch):
    """非 PDF（dxf/dwg）剖面不纳入本轮 VLM 兜底（最小改动范围）。"""
    drawings = [_structure_drawing("s1", file_key="s1.dxf")]
    calls: list[bytes] = []

    async def fake_read_drawing_vlm(image_bytes, *, timeout=None, **kwargs):
        calls.append(image_bytes)
        return VlmReadResult(backend="qwen3.5-vision", model="m")

    monkeypatch.setattr("core.model3d.vlm_read.read_drawing_vlm", fake_read_drawing_vlm)
    monkeypatch.setattr(model_builder, "get_file_bytes", lambda file_key: b"data")

    result = await model_builder._vlm_section_z_fallback(drawings, {})

    assert calls == []
    assert result == {}


# ── _recover_section_z：灰度开关决定是否调用兜底（零差异回归）────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_recover_section_z_skips_vlm_fallback_when_flag_off(monkeypatch):
    drawings = [_structure_drawing("s1")]

    class _EmptyNorm:
        stories_by_building: dict = {}

    monkeypatch.delenv(model_builder._VLM_SECTION_Z_ENV, raising=False)
    monkeypatch.setattr(model_builder, "get_file_bytes", lambda file_key: b"data")
    monkeypatch.setattr(model_builder, "_section_levels_sync", lambda data, ext: None)

    called = False

    async def spy(section_drawings, levels_by_drawing):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(model_builder, "_vlm_section_z_fallback", spy)

    await model_builder._recover_section_z(drawings, _EmptyNorm())

    assert called is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recover_section_z_calls_vlm_fallback_when_flag_on(monkeypatch):
    drawings = [_structure_drawing("s1")]

    class _EmptyNorm:
        stories_by_building: dict = {}

    monkeypatch.setenv(model_builder._VLM_SECTION_Z_ENV, "1")
    monkeypatch.setattr(model_builder, "get_file_bytes", lambda file_key: b"data")
    monkeypatch.setattr(model_builder, "_section_levels_sync", lambda data, ext: None)

    called = False

    async def spy(section_drawings, levels_by_drawing):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(model_builder, "_vlm_section_z_fallback", spy)

    await model_builder._recover_section_z(drawings, _EmptyNorm())

    assert called is True
