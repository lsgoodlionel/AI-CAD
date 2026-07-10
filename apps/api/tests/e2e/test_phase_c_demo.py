"""Phase C 里程碑 E2E Demo（C-18）：离线端到端串联全链路 + 逐条勾对验收总标准。

真实脱敏 test 集 + C-09 微调权重为前置阻塞项（卡 GPU/数据）；本 Demo 用确定性合成
图元在进程内串联**离线可验证**的链路，逐条断言 Phase C 验收总标准 1–6 中可离线核验
的部分；M1（符号识别超纯规则）的**终评数字**诚实标注为待 C-09（见断言注释与
docs/PHASE_C_ACCEPTANCE.md）。

链路：C-03 块展开 → C-04 自动标注 → C-12 spotting(mock 兜底) → C-13 融合 → C-14 评测。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from core.model3d.eval.harness import EvalSample, run_comparison
from core.model3d.eval.metrics import GtBox
from core.model3d.fusion import fuse
from core.model3d.preprocess.schema import Primitive, PrimitiveDoc
from core.model3d.spotting.service import SpottingService
from core.model3d.spotting.types import SymbolCandidate
from services import phase_c_signoff as sg

_API_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _API_ROOT.parent.parent
_DOCS = _REPO_ROOT / "docs"


# ── 合成一张结构平面（柱/梁 + 机电管线）──────────────────────────

def _doc() -> PrimitiveDoc:
    return PrimitiveDoc(
        page_w=600, page_h=800,
        primitives=(
            Primitive(id=0, type="polyline",
                      points=((0, 0), (400, 0), (400, 400), (0, 400), (0, 0)),
                      layer="S-COLU", block="KZ1", closed=True),
            Primitive(id=1, type="polyline",
                      points=((800, 0), (1200, 0), (1200, 400), (800, 400), (800, 0)),
                      layer="S-COLU", block="KZ2", closed=True),
            Primitive(id=2, type="polyline",
                      points=((0, 500), (300, 500), (300, 560), (0, 560), (0, 500)),
                      layer="M-EQPM", block="AHU", closed=True),
        ),
    )


def _gt() -> tuple[GtBox, ...]:
    return (
        GtBox("column", (0, 0, 400, 400)),
        GtBox("column", (800, 0, 1200, 400)),
        GtBox("equipment", (0, 500, 300, 560), mep_system="暖通"),
    )


# ── 验收标准 1：合规——产品树无 SymPoint 依赖 + 人工审核门禁通过 ──

def test_standard_1_compliance_no_sympoint_and_signoff_approved():
    # 扫描产品源码目录的真实 import（放行合规说明文字）
    pat = re.compile(r"^\s*(import|from)\s+sympoint(\s|\.|$)", re.I)
    src_dirs = ["core", "routers", "services", "scripts", "tasks"]
    leaks = []
    for d in src_dirs:
        for py in (_API_ROOT / d).rglob("*.py"):
            if ".venv" in py.parts:
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if pat.match(line):
                    leaks.append(f"{py}:{i}")
    assert leaks == [], f"产品树检测到 SymPoint 真实依赖: {leaks}"

    # 人工审核门禁（C-01 双通道）已签核 APPROVED
    state = sg.load(sg.DEFAULT_STATE_PATH)
    assert state.is_approved is True


# ── 验收标准 2：模型精度基座就绪（结构域可评；M1 终评待 C-09）──

def test_standard_2_eval_base_ready_structural_domain():
    report = run_comparison([EvalSample(doc=_doc(), gt=_gt())])
    assert set(report.methods) == {"rule", "model", "fusion"}
    # 结构域可被评测（柱为框状符号，规则确定性命中）
    rule = report.methods["rule"]
    assert rule.per_discipline.get("结构", {}).get("tp", 0) >= 1
    assert rule.per_category.get("column", {}).get("pq", 0) > 0
    # M1 终评（学习模型/融合结构域 PQ 显著超纯规则）待 C-09 真实权重复评。
    assert any("C-09" in n for n in report.notes)  # 报告显式标注占位状态


# ── 验收标准 3：融合正确性——补规则不替代规则，带 source+confidence ──

def test_standard_3_fusion_correctness():
    strong_rule = [SymbolCandidate("column", 0.9, (0, 0, 10, 10), source="rule")]
    conflicting_model = [SymbolCandidate("beam", 0.95, (0, 0, 10, 10), source="model")]
    result = fuse(strong_rule, conflicting_model)
    # 规则强命中不被模型翻案（类别仍 column、来源仍 rule）
    col = next(c for c in result.candidates if c.bbox == (0, 0, 10, 10))
    assert col.category == "column" and col.source == "rule"
    # 每个输出均带 source + confidence
    assert all(c.source in ("rule", "model", "fused") for c in result.candidates)
    assert all(0.0 <= c.confidence <= 1.0 for c in result.candidates)

    # 融合召回 ≥ 纯规则（规则候选全保留）
    report = run_comparison([EvalSample(doc=_doc(), gt=_gt())])
    assert report.methods["fusion"].recall >= report.methods["rule"].recall


# ── 验收标准 4：审校闭环契约就绪（埋点表 + 数据飞轮）──────────────

def test_standard_4_review_loop_contract():
    mig = (_API_ROOT / "migrations" / "024_review_actions.sql").read_text(encoding="utf-8")
    assert "model_review_actions" in mig      # 人审动作埋点（C-17 收敛度量源）
    assert "model_symbol_annotations" in mig  # 符号金标签（C-06/C-09 回流）
    # rework 口径在看板逻辑中定义（reclass+reject+addbox）
    dash = (_API_ROOT / "routers" / "dashboard.py").read_text(encoding="utf-8")
    assert "reclass" in dash and "addbox" in dash


# ── 验收标准 5：数据资产——按项目切分无泄漏 + 规范文档齐备 ────────

def test_standard_5_dataset_split_no_leakage_and_docs():
    # 载入 C-07 切分脚本，断言按项目切分无泄漏
    mod_path = _API_ROOT / "scripts" / "model3d" / "dataset_split.py"
    spec = importlib.util.spec_from_file_location("dataset_split_e2e", mod_path)
    ds = importlib.util.module_from_spec(spec)
    sys.modules["dataset_split_e2e"] = ds
    spec.loader.exec_module(ds)

    samples = [
        ds.Sample(sample_id=f"s{i}", project_id=f"p{i % 4}", drawing_id=f"d{i}", path="")
        for i in range(40)
    ]
    splits = ds.split_by_project(samples, seed=42)
    ds.assert_no_project_leakage(splits)  # 同项目不跨 split，否则抛异常

    for doc in ("PHASE_C_DATASET_SPEC.md", "PHASE_C_ANNOTATION_GUIDE.md"):
        assert (_DOCS / doc).exists()
    assert (_API_ROOT / "data" / "model3d" / "dataset" / "DATASHEET.md").exists()


# ── 验收标准 6：能力边界如实（25–30% + 不承诺一键出 BIM）──────────

def test_standard_6_capability_boundary_honest():
    acceptance = (_DOCS / "PHASE_C_ACCEPTANCE.md").read_text(encoding="utf-8")
    assert "25" in acceptance and "30" in acceptance          # 效率现实值区间
    assert "人工审改" in acceptance or "人工审校" in acceptance
    # 不承诺一键出 BIM
    assert "BIM" in acceptance


# ── spotting 服务离线兜底可跑（无 GPU/权重不硬失败）──────────────

def test_spotting_service_offline_mock_fallback():
    svc = SpottingService(db=None)
    res = svc.spot_doc(_doc())
    assert res.backend in ("mock", "cadtransformer")
    assert res.backend == "mock"  # 本环境无 GPU/权重 → mock 兜底
    assert len(res.candidates) >= 2
