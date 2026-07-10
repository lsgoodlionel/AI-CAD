"""services/fragments_convert 单测：IFC→Fragments 子进程封装的成功/失败路径。

全部 mock 掉 subprocess/node/依赖检查，无需真实 Node 环境；只验证本模块的
错误分类、产物校验与 building_key 规整逻辑。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services import fragments_convert
from services.fragments_convert import FragmentsConversionError


@pytest.fixture
def ifc_file(tmp_path):
    path = tmp_path / "model.ifc"
    path.write_bytes(b"ISO-10303-21;")
    return path


def _bypass_env(monkeypatch):
    """跳过依赖/二进制探测，聚焦子进程与产物逻辑。"""
    monkeypatch.setattr(fragments_convert, "_ensure_converter_ready", lambda: None)
    monkeypatch.setattr(fragments_convert, "_resolve_node_binary", lambda: "node")


def _fake_run_writing(payload: bytes):
    def _run(cmd, **kwargs):  # cmd = [node, script, ifc_path, frag_path]
        Path(cmd[3]).write_bytes(payload)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    return _run


# ── 成功路径 ────────────────────────────────────────────────────


def test_convert_file_success(monkeypatch, tmp_path, ifc_file):
    _bypass_env(monkeypatch)
    frag = tmp_path / "model.frag"
    monkeypatch.setattr(fragments_convert.subprocess, "run", _fake_run_writing(b"FRAGBYTES"))

    out = fragments_convert.convert_ifc_file_to_fragments(ifc_file, frag)

    assert out == frag
    assert frag.read_bytes() == b"FRAGBYTES"


def test_convert_bytes_success(monkeypatch):
    _bypass_env(monkeypatch)
    monkeypatch.setattr(fragments_convert.subprocess, "run", _fake_run_writing(b"FRAG"))

    result = fragments_convert.convert_ifc_bytes_to_fragments(b"ISO-10303-21;")

    assert result == b"FRAG"


# ── 子进程失败分类 ──────────────────────────────────────────────


def test_nonzero_exit_raises(monkeypatch, tmp_path, ifc_file):
    _bypass_env(monkeypatch)
    monkeypatch.setattr(
        fragments_convert.subprocess, "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"),
    )
    with pytest.raises(FragmentsConversionError, match="exit=1"):
        fragments_convert.convert_ifc_file_to_fragments(ifc_file, tmp_path / "o.frag")


def test_timeout_raises(monkeypatch, tmp_path, ifc_file):
    _bypass_env(monkeypatch)

    def _run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(fragments_convert.subprocess, "run", _run)
    with pytest.raises(FragmentsConversionError, match="超时"):
        fragments_convert.convert_ifc_file_to_fragments(ifc_file, tmp_path / "o.frag")


def test_subprocess_oserror_raises(monkeypatch, tmp_path, ifc_file):
    _bypass_env(monkeypatch)

    def _run(cmd, **kwargs):
        raise OSError("cannot exec node")

    monkeypatch.setattr(fragments_convert.subprocess, "run", _run)
    with pytest.raises(FragmentsConversionError, match="启动转换子进程失败"):
        fragments_convert.convert_ifc_file_to_fragments(ifc_file, tmp_path / "o.frag")


# ── 产物校验 ────────────────────────────────────────────────────


def test_missing_output_raises(monkeypatch, tmp_path, ifc_file):
    _bypass_env(monkeypatch)
    monkeypatch.setattr(  # returncode 0 但不写出 .frag
        fragments_convert.subprocess, "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    with pytest.raises(FragmentsConversionError, match="未产出有效"):
        fragments_convert.convert_ifc_file_to_fragments(ifc_file, tmp_path / "o.frag")


def test_empty_output_raises(monkeypatch, tmp_path, ifc_file):
    _bypass_env(monkeypatch)
    monkeypatch.setattr(fragments_convert.subprocess, "run", _fake_run_writing(b""))
    with pytest.raises(FragmentsConversionError, match="未产出有效"):
        fragments_convert.convert_ifc_file_to_fragments(ifc_file, tmp_path / "o.frag")


def test_missing_input_raises(monkeypatch, tmp_path):
    _bypass_env(monkeypatch)
    with pytest.raises(FragmentsConversionError, match="输入 IFC 不存在"):
        fragments_convert.convert_ifc_file_to_fragments(
            tmp_path / "nope.ifc", tmp_path / "o.frag"
        )


def test_empty_ifc_bytes_rejected():
    with pytest.raises(FragmentsConversionError, match="为空"):
        fragments_convert.convert_ifc_bytes_to_fragments(b"")


# ── 依赖 / 二进制探测 ───────────────────────────────────────────


def test_node_binary_missing_raises(monkeypatch):
    monkeypatch.delenv("NODE_BINARY", raising=False)
    monkeypatch.setattr(fragments_convert.shutil, "which", lambda name: None)
    with pytest.raises(FragmentsConversionError, match="未找到 node"):
        fragments_convert._resolve_node_binary()


def test_node_binary_env_override(monkeypatch):
    monkeypatch.setenv("NODE_BINARY", "/opt/node/bin/node")
    assert fragments_convert._resolve_node_binary() == "/opt/node/bin/node"


def test_convert_dir_env_override(monkeypatch):
    """容器内布局：MODEL_CONVERT_DIR 覆盖转换器目录定位。"""
    monkeypatch.setenv("MODEL_CONVERT_DIR", "/opt/model-convert")
    assert fragments_convert._resolve_convert_dir() == Path("/opt/model-convert")


def test_convert_dir_defaults_to_source_tree(monkeypatch):
    """未设 MODEL_CONVERT_DIR 时回退源码树默认路径（本地/CI）。"""
    monkeypatch.delenv("MODEL_CONVERT_DIR", raising=False)
    assert fragments_convert._resolve_convert_dir() == fragments_convert._DEFAULT_CONVERT_DIR


def test_converter_missing_script_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(fragments_convert, "_CONVERT_SCRIPT", tmp_path / "missing.mjs")
    monkeypatch.setattr(fragments_convert, "_CONVERT_DIR", tmp_path)
    with pytest.raises(FragmentsConversionError, match="转换脚本缺失"):
        fragments_convert._ensure_converter_ready()


def test_converter_missing_fragments_dep_raises(monkeypatch, tmp_path):
    script = tmp_path / "ifc_to_fragments.mjs"
    script.write_text("// stub")
    monkeypatch.setattr(fragments_convert, "_CONVERT_SCRIPT", script)
    monkeypatch.setattr(fragments_convert, "_CONVERT_DIR", tmp_path)  # 无 node_modules
    with pytest.raises(FragmentsConversionError, match="@thatopen/fragments"):
        fragments_convert._ensure_converter_ready()


# ── building_key 规整（MinIO key 安全）──────────────────────────


def test_object_key_normal():
    assert (
        fragments_convert.fragments_object_key(7, "main")
        == "projects/7/model_ifc/main.frag"
    )


def test_object_key_strips_path_traversal():
    key = fragments_convert.fragments_object_key(7, "../../etc/passwd")
    assert ".." not in key
    assert "/etc/" not in key
    assert key.startswith("projects/7/model_ifc/")
    assert key.endswith(".frag")


def test_object_key_all_illegal_falls_back():
    assert (
        fragments_convert.fragments_object_key(7, "###")
        == "projects/7/model_ifc/building.frag"
    )


def test_object_key_truncates_long_input():
    key = fragments_convert.fragments_object_key(7, "b" * 200)
    slug = key.rsplit("/", 1)[-1].removesuffix(".frag")
    assert len(slug) <= 64
