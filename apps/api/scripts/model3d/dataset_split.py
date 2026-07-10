#!/usr/bin/env python3
"""C-07 数据集切分：按**项目**可复现地切 train/val/test（防泄漏）。

灵魂约束：
  - **按 project 切，绝不按 drawing 切**——同一 ``project_id`` 的所有样本必须
    整体落在同一个 split，否则同项目样本跨切分泄漏 → 评测虚高。
  - **可一键复现**：固定随机种子，同输入同种子 → 同切分（确定性）。
  - **test 集冻结**：切分清单固化后 test 不再变（仅 C-18 终评解锁一次）。

设计：纯逻辑、不可变（frozen dataclass）、优雅降级（空清单/畸形条目安全处理）。
不引入第三方依赖（DVC 可选，由主控管）。

CLI：
  python apps/api/scripts/model3d/dataset_split.py manifest.json --out splits.json --seed 42
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

# ── 常量 ──────────────────────────────────────────────────────

SPLIT_NAMES: tuple[str, str, str] = ("train", "val", "test")
DEFAULT_RATIOS: tuple[float, float, float] = (0.8, 0.1, 0.1)
DEFAULT_SEED: int = 42
_RATIO_TOLERANCE = 1e-9


# ── 契约 ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Sample:
    """数据集样本清单条目（不可变）。

    切分只依赖 ``project_id``；其余字段透传，供数据卡统计与训练消费。
    ``meta`` 常含 ``discipline``（专业：structure/mep/decoration…）用于分布统计。
    """

    sample_id: str
    project_id: str
    drawing_id: str
    path: str
    meta: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "project_id": self.project_id,
            "drawing_id": self.drawing_id,
            "path": self.path,
            "meta": dict(self.meta),
        }

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> "Sample":
        """从清单条目构造 Sample；缺字段抛 ValueError（由调用方决定是否跳过）。"""
        if not isinstance(raw, Mapping):
            raise ValueError("样本条目必须是对象")
        try:
            sample_id = str(raw["sample_id"])
            project_id = str(raw["project_id"])
        except (KeyError, TypeError) as exc:
            raise ValueError(f"样本缺少必填字段 sample_id/project_id: {raw!r}") from exc
        if not project_id:
            raise ValueError(f"样本 project_id 为空: {raw!r}")
        meta = raw.get("meta") or {}
        if not isinstance(meta, Mapping):
            meta = {}
        return Sample(
            sample_id=sample_id,
            project_id=project_id,
            drawing_id=str(raw.get("drawing_id", "")),
            path=str(raw.get("path", "")),
            meta=dict(meta),
        )


class ProjectLeakageError(AssertionError):
    """同一 project_id 跨多个 split → 泄漏，切分不可用。"""


# ── 核心切分 ──────────────────────────────────────────────────


def _normalize_ratios(ratios: Sequence[float]) -> tuple[float, float, float]:
    """校验并归一化三元比例；非法输入优雅回退到默认。"""
    if not ratios or len(ratios) != 3:
        return DEFAULT_RATIOS
    try:
        vals = [float(r) for r in ratios]
    except (TypeError, ValueError):
        return DEFAULT_RATIOS
    if any(v < 0 for v in vals):
        return DEFAULT_RATIOS
    total = sum(vals)
    if total <= _RATIO_TOLERANCE:
        return DEFAULT_RATIOS
    return (vals[0] / total, vals[1] / total, vals[2] / total)


def _group_by_project(samples: Sequence[Sample]) -> dict[str, list[Sample]]:
    """按 project_id 聚合，project 顺序按 project_id 升序（确定性基序）。"""
    groups: dict[str, list[Sample]] = {}
    for s in samples:
        groups.setdefault(s.project_id, []).append(s)
    return {pid: groups[pid] for pid in sorted(groups)}


def _assign_projects(
    project_ids: Sequence[str],
    sizes: Mapping[str, int],
    ratios: tuple[float, float, float],
) -> dict[str, list[str]]:
    """把（已洗牌的）项目贪心分配到最欠额的 split，逼近**样本级**比例。

    对每个项目，选当前「目标样本数 - 已分配样本数」缺口最大的 split；
    平票按 train>val>test 固定顺序。整体确定性（不含随机）。
    """
    total_samples = sum(sizes[pid] for pid in project_ids)
    targets = {name: ratios[i] * total_samples for i, name in enumerate(SPLIT_NAMES)}
    assigned: dict[str, list[str]] = {name: [] for name in SPLIT_NAMES}
    counts: dict[str, int] = {name: 0 for name in SPLIT_NAMES}

    for pid in project_ids:
        best_name = SPLIT_NAMES[0]
        best_deficit = float("-inf")
        for name in SPLIT_NAMES:
            deficit = targets[name] - counts[name]
            if deficit > best_deficit + _RATIO_TOLERANCE:
                best_deficit = deficit
                best_name = name
        assigned[best_name].append(pid)
        counts[best_name] += sizes[pid]
    return assigned


def split_by_project(
    samples: Sequence[Sample],
    *,
    ratios: Sequence[float] = DEFAULT_RATIOS,
    seed: int = DEFAULT_SEED,
) -> dict[str, list[Sample]]:
    """按项目确定性切分为 train/val/test。

    步骤：按 project_id 聚合 → 升序基序上做**种子化洗牌** → 贪心逼近样本比例分配。
    保证：同 project_id 不跨 split；同输入同 seed → 同结果。

    空清单返回三个空列表；畸形样本应在入口（CLI/from_dict）过滤。
    """
    norm_ratios = _normalize_ratios(ratios)
    groups = _group_by_project(samples)
    project_ids = list(groups.keys())  # 已按 project_id 升序（确定性）

    rng = random.Random(seed)
    rng.shuffle(project_ids)  # 就地洗牌确定性：同 seed 同结果

    sizes = {pid: len(groups[pid]) for pid in project_ids}
    assigned = _assign_projects(project_ids, sizes, norm_ratios)

    result: dict[str, list[Sample]] = {}
    for name in SPLIT_NAMES:
        bucket: list[Sample] = []
        for pid in assigned[name]:
            bucket.extend(groups[pid])
        result[name] = bucket
    return result


# ── 校验 ──────────────────────────────────────────────────────


def assert_no_project_leakage(splits: Mapping[str, Sequence[Sample]]) -> None:
    """断言无同项目跨 split 泄漏；有则抛 ProjectLeakageError。"""
    seen: dict[str, str] = {}
    for name, bucket in splits.items():
        for s in bucket:
            prev = seen.get(s.project_id)
            if prev is not None and prev != name:
                raise ProjectLeakageError(
                    f"项目 {s.project_id!r} 同时出现在 {prev!r} 与 {name!r} → 泄漏"
                )
            seen[s.project_id] = name


def _split_signature(splits: Mapping[str, Sequence[Sample]]) -> dict[str, list[str]]:
    """把切分归约为「split -> 有序 sample_id 列表」，用于逐位比对复现性。"""
    return {name: [s.sample_id for s in splits.get(name, [])] for name in SPLIT_NAMES}


def verify_reproducible(
    samples: Sequence[Sample],
    *,
    ratios: Sequence[float] = DEFAULT_RATIOS,
    seed: int = DEFAULT_SEED,
) -> bool:
    """同输入同 seed 连切两次，逐位一致返回 True。"""
    first = _split_signature(split_by_project(samples, ratios=ratios, seed=seed))
    second = _split_signature(split_by_project(samples, ratios=ratios, seed=seed))
    return first == second


# ── 统计 ──────────────────────────────────────────────────────


def dataset_statistics(splits: Mapping[str, Sequence[Sample]]) -> dict[str, Any]:
    """各 split 的项目数/样本数/分专业分布，供数据卡与复现审计。"""
    stats: dict[str, Any] = {"splits": {}, "total_samples": 0, "total_projects": 0}
    all_projects: set[str] = set()
    for name in SPLIT_NAMES:
        bucket = list(splits.get(name, []))
        projects = {s.project_id for s in bucket}
        disciplines: dict[str, int] = {}
        for s in bucket:
            disc = str(s.meta.get("discipline", "unknown"))
            disciplines[disc] = disciplines.get(disc, 0) + 1
        stats["splits"][name] = {
            "projects": len(projects),
            "samples": len(bucket),
            "disciplines": dict(sorted(disciplines.items())),
        }
        stats["total_samples"] += len(bucket)
        all_projects |= projects
    stats["total_projects"] = len(all_projects)
    return stats


# ── 清单 IO ───────────────────────────────────────────────────


def load_manifest(raw: Any) -> tuple[list[Sample], list[str]]:
    """从已解析 JSON 载入样本清单，返回 (samples, warnings)。

    接受顶层为 list，或 ``{"samples": [...]}``。畸形条目跳过并记 warning。
    """
    warnings: list[str] = []
    if isinstance(raw, Mapping):
        raw = raw.get("samples", [])
    if not isinstance(raw, list):
        warnings.append("清单顶层非数组/无 samples 字段，视为空清单")
        return [], warnings

    samples: list[Sample] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(raw):
        try:
            sample = Sample.from_dict(item)
        except ValueError as exc:
            warnings.append(f"跳过第 {idx} 条畸形样本：{exc}")
            continue
        if sample.sample_id in seen_ids:
            warnings.append(f"跳过重复 sample_id：{sample.sample_id!r}")
            continue
        seen_ids.add(sample.sample_id)
        samples.append(sample)
    return samples, warnings


def build_split_manifest(
    splits: Mapping[str, Sequence[Sample]],
    *,
    seed: int,
    ratios: Sequence[float],
) -> dict[str, Any]:
    """构造可固化、可复现的切分清单（含种子/比例/统计与逐 split 样本）。"""
    return {
        "version": 1,
        "split_by": "project",
        "seed": seed,
        "ratios": list(_normalize_ratios(ratios)),
        "statistics": dataset_statistics(splits),
        "splits": {
            name: [s.to_dict() for s in splits.get(name, [])] for name in SPLIT_NAMES
        },
    }


# ── CLI ───────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="C-07 按项目切分数据集为 train/val/test（可复现，防泄漏）"
    )
    parser.add_argument("manifest", help="样本清单 JSON（list 或 {samples:[...]}）")
    parser.add_argument("--out", default=None, help="切分清单输出 JSON（默认打印到 stdout）")
    parser.add_argument(
        "--ratios",
        default="0.8,0.1,0.1",
        help="train,val,test 比例（自动归一化，默认 0.8,0.1,0.1）",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="随机种子（默认 42）")
    args = parser.parse_args(argv)

    src = Path(args.manifest)
    if not src.exists():
        print(f"错误：清单不存在 {src}", file=sys.stderr)
        return 2
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"错误：无法解析清单 {src}：{exc}", file=sys.stderr)
        return 2

    try:
        ratios = tuple(float(x) for x in args.ratios.split(","))
    except ValueError:
        print("错误：--ratios 格式应为 a,b,c", file=sys.stderr)
        return 2

    samples, warnings = load_manifest(raw)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)

    splits = split_by_project(samples, ratios=ratios, seed=args.seed)
    assert_no_project_leakage(splits)  # 灵魂约束：产出前自检

    manifest = build_split_manifest(splits, seed=args.seed, ratios=ratios)
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        print(f"切分清单 → {out_path}")
    else:
        print(payload)

    stats = manifest["statistics"]
    print(
        f"统计: 项目 {stats['total_projects']} / 样本 {stats['total_samples']}  "
        + "  ".join(
            f"{n}={stats['splits'][n]['projects']}p/{stats['splits'][n]['samples']}s"
            for n in SPLIT_NAMES
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
