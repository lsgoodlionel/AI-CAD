"""C-07 数据集切分测试（TDD）。

核心断言：
  - 按项目切分**无泄漏**（同 project_id 不跨 split）；
  - 固定种子**可复现**（两次调用逐位一致）；
  - 比例**近似正确**；
  - 空/单项目/畸形输入**优雅处理**；
  - test 集**确定性**（同 seed 恒定）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# 直接从 scripts/model3d/ 载入被测模块（非包，用 importlib 按路径加载）
_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "model3d" / "dataset_split.py"
)
_spec = importlib.util.spec_from_file_location("dataset_split", _MODULE_PATH)
assert _spec and _spec.loader
ds = importlib.util.module_from_spec(_spec)
sys.modules["dataset_split"] = ds  # dataclass/typing 解析需模块在 sys.modules 中
_spec.loader.exec_module(ds)


# ── 夹具 ──────────────────────────────────────────────────────


def _make_samples(n_projects: int, per_project: int, discipline: str = "structure"):
    samples = []
    for p in range(n_projects):
        pid = f"proj-{p:03d}"
        for d in range(per_project):
            samples.append(
                ds.Sample(
                    sample_id=f"{pid}-draw-{d}",
                    project_id=pid,
                    drawing_id=f"draw-{d}",
                    path=f"/data/{pid}/{d}.json",
                    meta={"discipline": discipline},
                )
            )
    return samples


def _project_of(splits, name):
    return {s.project_id for s in splits[name]}


# ── 无泄漏（灵魂约束）────────────────────────────────────────


@pytest.mark.unit
def test_no_project_leakage_across_splits():
    # Arrange
    samples = _make_samples(30, 4)
    # Act
    splits = ds.split_by_project(samples, seed=42)
    # Assert：同项目集合两两不相交
    train_p = _project_of(splits, "train")
    val_p = _project_of(splits, "val")
    test_p = _project_of(splits, "test")
    assert train_p.isdisjoint(val_p)
    assert train_p.isdisjoint(test_p)
    assert val_p.isdisjoint(test_p)
    ds.assert_no_project_leakage(splits)  # 不抛异常


@pytest.mark.unit
def test_every_sample_of_a_project_stays_together():
    samples = _make_samples(12, 5)
    splits = ds.split_by_project(samples, seed=7)
    # 每个项目的全部样本必须在同一个 split
    location: dict[str, str] = {}
    for name, bucket in splits.items():
        for s in bucket:
            if s.project_id in location:
                assert location[s.project_id] == name
            location[s.project_id] = name


@pytest.mark.unit
def test_assert_no_project_leakage_raises_on_manual_leak():
    s = ds.Sample("a", "P1", "d", "/p")
    leaked = {"train": [s], "val": [s], "test": []}
    with pytest.raises(ds.ProjectLeakageError):
        ds.assert_no_project_leakage(leaked)


@pytest.mark.unit
def test_no_sample_lost_or_duplicated():
    samples = _make_samples(20, 3)
    splits = ds.split_by_project(samples, seed=1)
    all_ids = [s.sample_id for bucket in splits.values() for s in bucket]
    assert sorted(all_ids) == sorted(s.sample_id for s in samples)
    assert len(all_ids) == len(set(all_ids))  # 无重复


# ── 可复现 ────────────────────────────────────────────────────


@pytest.mark.unit
def test_reproducible_same_seed_identical():
    samples = _make_samples(25, 4)
    a = ds.split_by_project(samples, seed=42)
    b = ds.split_by_project(samples, seed=42)
    for name in ds.SPLIT_NAMES:
        assert [s.sample_id for s in a[name]] == [s.sample_id for s in b[name]]
    assert ds.verify_reproducible(samples, seed=42) is True


@pytest.mark.unit
def test_different_seed_generally_differs():
    samples = _make_samples(40, 2)
    a = ds.split_by_project(samples, seed=1)
    b = ds.split_by_project(samples, seed=999)
    # 不同种子应产生不同的 test 集项目划分（极大概率）
    assert _project_of(a, "test") != _project_of(b, "test")


@pytest.mark.unit
def test_test_set_deterministic_frozen():
    samples = _make_samples(30, 3)
    first_test = {s.sample_id for s in ds.split_by_project(samples, seed=42)["test"]}
    for _ in range(3):
        again = {s.sample_id for s in ds.split_by_project(samples, seed=42)["test"]}
        assert again == first_test


@pytest.mark.unit
def test_input_order_does_not_change_split():
    samples = _make_samples(18, 3)
    reversed_samples = list(reversed(samples))
    a = ds.split_by_project(samples, seed=42)
    b = ds.split_by_project(reversed_samples, seed=42)
    for name in ds.SPLIT_NAMES:
        assert _project_of(a, name) == _project_of(b, name)


# ── 比例近似 ──────────────────────────────────────────────────


@pytest.mark.unit
def test_ratios_approximately_correct():
    samples = _make_samples(100, 2)  # 200 样本，项目等大
    splits = ds.split_by_project(samples, ratios=(0.8, 0.1, 0.1), seed=42)
    total = len(samples)
    assert abs(len(splits["train"]) / total - 0.8) < 0.08
    assert abs(len(splits["val"]) / total - 0.1) < 0.08
    assert abs(len(splits["test"]) / total - 0.1) < 0.08


@pytest.mark.unit
def test_ratios_normalized_when_not_summing_to_one():
    samples = _make_samples(50, 2)
    # 未归一化比例（8:1:1）应等价于 0.8/0.1/0.1
    splits = ds.split_by_project(samples, ratios=(8, 1, 1), seed=42)
    total = len(samples)
    assert abs(len(splits["train"]) / total - 0.8) < 0.1


@pytest.mark.unit
def test_illegal_ratios_fall_back_to_default():
    samples = _make_samples(20, 2)
    for bad in [(0, 0, 0), (-1, 2, 3), (0.5, 0.5), None]:
        splits = ds.split_by_project(samples, ratios=bad, seed=42)  # type: ignore[arg-type]
        assert sum(len(splits[n]) for n in ds.SPLIT_NAMES) == len(samples)
        ds.assert_no_project_leakage(splits)


# ── 优雅降级 ──────────────────────────────────────────────────


@pytest.mark.unit
def test_empty_manifest():
    splits = ds.split_by_project([], seed=42)
    assert splits == {"train": [], "val": [], "test": []}
    ds.assert_no_project_leakage(splits)
    assert ds.verify_reproducible([], seed=42) is True


@pytest.mark.unit
def test_single_project_no_leakage():
    samples = _make_samples(1, 6)
    splits = ds.split_by_project(samples, seed=42)
    ds.assert_no_project_leakage(splits)
    # 单项目只能整体落在一个 split
    non_empty = [n for n in ds.SPLIT_NAMES if splits[n]]
    assert len(non_empty) == 1
    assert len(splits[non_empty[0]]) == 6


@pytest.mark.unit
def test_from_dict_rejects_malformed():
    with pytest.raises(ValueError):
        ds.Sample.from_dict({"drawing_id": "d"})  # 缺 sample_id/project_id
    with pytest.raises(ValueError):
        ds.Sample.from_dict({"sample_id": "a", "project_id": ""})  # 空 project_id
    with pytest.raises(ValueError):
        ds.Sample.from_dict("not-a-dict")  # type: ignore[arg-type]


@pytest.mark.unit
def test_load_manifest_skips_malformed_and_dedups():
    raw = [
        {"sample_id": "a", "project_id": "P1", "drawing_id": "d1", "path": "/a"},
        {"drawing_id": "broken"},  # 畸形 → 跳过
        {"sample_id": "a", "project_id": "P1"},  # 重复 id → 跳过
        {"sample_id": "b", "project_id": "P2", "meta": "bad-meta"},  # meta 非对象 → 归空
    ]
    samples, warnings = ds.load_manifest(raw)
    assert [s.sample_id for s in samples] == ["a", "b"]
    assert len(warnings) == 2
    assert samples[1].meta == {}


@pytest.mark.unit
def test_load_manifest_accepts_object_wrapper_and_bad_top_level():
    wrapped = {"samples": [{"sample_id": "x", "project_id": "P"}]}
    samples, warnings = ds.load_manifest(wrapped)
    assert len(samples) == 1 and warnings == []

    empty, warns = ds.load_manifest(12345)  # 顶层非法
    assert empty == [] and len(warns) == 1


# ── 统计与清单 ────────────────────────────────────────────────


@pytest.mark.unit
def test_dataset_statistics_counts_projects_samples_disciplines():
    samples = _make_samples(10, 2, discipline="structure") + _make_samples(
        4, 3, discipline="mep"
    )
    # 避免 project_id 冲突：重打 mep 项目号
    mep = [
        ds.Sample(f"mep-{i}", f"mepproj-{i}", "d", "/p", {"discipline": "mep"})
        for i in range(4)
    ]
    samples = _make_samples(10, 2, discipline="structure") + mep
    splits = ds.split_by_project(samples, seed=42)
    stats = ds.dataset_statistics(splits)
    assert stats["total_samples"] == len(samples)
    assert stats["total_projects"] == 14
    # 各 split 都带专业分布字典
    for n in ds.SPLIT_NAMES:
        assert "disciplines" in stats["splits"][n]


@pytest.mark.unit
def test_missing_discipline_counts_as_unknown():
    samples = [ds.Sample("a", "P1", "d", "/p", {})]
    splits = ds.split_by_project(samples, seed=42)
    stats = ds.dataset_statistics(splits)
    train = stats["splits"]["train"]["disciplines"]
    assert train.get("unknown") == 1


@pytest.mark.unit
def test_build_split_manifest_roundtrip_json():
    samples = _make_samples(12, 2)
    splits = ds.split_by_project(samples, seed=42)
    manifest = ds.build_split_manifest(splits, seed=42, ratios=(0.8, 0.1, 0.1))
    assert manifest["split_by"] == "project"
    assert manifest["seed"] == 42
    # 可 JSON 序列化并复原样本
    text = json.dumps(manifest, ensure_ascii=False)
    reloaded = json.loads(text)
    restored, _ = ds.load_manifest(
        [s for n in ds.SPLIT_NAMES for s in reloaded["splits"][n]]
    )
    assert len(restored) == len(samples)


@pytest.mark.unit
def test_sample_to_dict_from_dict_roundtrip():
    s = ds.Sample("a", "P1", "d1", "/x", {"discipline": "mep", "k": 1})
    back = ds.Sample.from_dict(s.to_dict())
    assert back == s


# ── CLI ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_writes_split_manifest(tmp_path):
    manifest_in = tmp_path / "manifest.json"
    out = tmp_path / "splits.json"
    samples = [s.to_dict() for s in _make_samples(20, 2)]
    manifest_in.write_text(json.dumps(samples), encoding="utf-8")

    rc = ds.main([str(manifest_in), "--out", str(out), "--seed", "42"])
    assert rc == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["split_by"] == "project"
    assert written["seed"] == 42
    total = sum(len(written["splits"][n]) for n in ds.SPLIT_NAMES)
    assert total == len(samples)


@pytest.mark.unit
def test_cli_missing_file_returns_error():
    rc = ds.main(["/no/such/manifest.json", "--seed", "42"])
    assert rc == 2


@pytest.mark.unit
def test_cli_bad_json_returns_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert ds.main([str(bad)]) == 2


@pytest.mark.unit
def test_cli_bad_ratios_returns_error(tmp_path):
    manifest_in = tmp_path / "m.json"
    manifest_in.write_text(json.dumps([{"sample_id": "a", "project_id": "P"}]))
    rc = ds.main([str(manifest_in), "--ratios", "x,y,z"])
    assert rc == 2


@pytest.mark.unit
def test_cli_stdout_when_no_out(tmp_path, capsys):
    manifest_in = tmp_path / "m.json"
    manifest_in.write_text(json.dumps([s.to_dict() for s in _make_samples(6, 2)]))
    rc = ds.main([str(manifest_in), "--seed", "42"])
    assert rc == 0
    captured = capsys.readouterr()
    assert '"split_by": "project"' in captured.out
