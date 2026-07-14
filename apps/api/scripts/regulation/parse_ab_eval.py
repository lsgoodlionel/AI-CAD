#!/usr/bin/env python3
"""D-17 规范 PDF→条文抽取 离线 A/B 评测（docling vs 既有 pymupdf4llm→pymupdf 链）。

对比维度（三项，均为确定性可复现指标，无 LLM 调用）：
  1. **条文编号抽取召回率**——用既有 `regulation_importer.split_into_paragraphs`
     从各后端产出的文本里抽条文编号（如 "4.2.3"），与金标准编号集合做多重集
     精确匹配，算 Precision/Recall/F1。这是下游 NLP 提取流水线
     （Haiku 分类→Sonnet 深提取→AGE/Chroma）能否对上正确条文的前提——抽取
     阶段漏掉或读串的编号，下游模型分类/提取得再准也救不回来。
  2. **条文顺序保真**（多栏排版是否被正确还原为线性阅读顺序）——命中的条文
     编号序列与金标准序列的最长公共子序列（LCS）占比。F1 只看「有没有抽
     到」，抽到了但顺序被双栏交错打乱（常见于规范正文双栏排版）不会体现在
     F1 上，必须单独度量。
  3. **表格结构保真**——用正则探测输出文本里是否出现 Markdown 表格分隔行
     （``| --- | --- |``），与金标准「该样本是否含需保真表格」做二分类
     Precision/Recall/F1。这是**代理指标**（proxy），只判定"表格是否被保留
     为结构化表格语法"，不比对表格内容/单元格数值——语义级表格内容比对
     需要逐单元格金标注，超出本轮评测口径，见文末"已知局限"。

后端：
  - ``docling``：`core/regulation/docling_extract.extract_with_docling`
    （docling 未安装时该样本记入 unavailable，不参与打分，不用 0 分冒充
    "跑过"——与 D-16 OCR 评测基座同一纪律）。
  - ``pymupdf4llm_chain``：D-17 之前的既有降级链（pymupdf4llm→pymupdf 原始
    文本），本脚本内独立重新实现（不经过 docling 前段），作为 A/B 的基线。
  - ``current_pipeline``：`services/regulation_importer.extract_text_from_pdf`
    当前生产优先级链的实际输出（docling 可用则含 docling，否则等同基线）——
    用于确认"默认行为在 docling 缺失时确实不变"。

用法：
  # 合成 demo（无需真实规范 PDF/docling 依赖，验证基座端到端）
  python scripts/regulation/parse_ab_eval.py --demo

  # 真实评测：manifest JSON = {"samples":[{"sample_id","path","gold":{
  #   "article_nos":["4.2.1","4.2.2","4.2.3"], "table_expected":true}}]}
  # path 相对 manifest 所在目录解析（也接受绝对路径）。
  python scripts/regulation/parse_ab_eval.py --manifest manifest.json \\
      --out docs/parse_ab_report.md

已知局限（如实标注，不夸大结论）：
  - 表格指标是结构语法代理指标，不是单元格级内容比对。
  - 条文编号抽取复用现有 `split_into_paragraphs` 正则口径，该口径本身的
    误差（如把非条文的编号样式误判为条文）会同等程度影响两个后端，属于
    共模误差，不影响 A/B 相对比较的有效性，但会影响绝对数值的可解释性。
  - 本脚本不修改 `regulation_importer.py` 的默认优先级链，仅供人工/CI 参考
    是否需要调整。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[2]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from core.regulation.docling_extract import extract_with_docling  # noqa: E402
from services.regulation_importer import (  # noqa: E402
    extract_text_from_pdf as current_pipeline_extract,
)
from services.regulation_importer import split_into_paragraphs  # noqa: E402

_TABLE_SEPARATOR_ROW = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|[\s:|-]*\s*$")
_ARTICLE_NO_PREFIX = re.compile(r"^\s*(\d+(?:\.\d+){0,4})\s*")


# ── 后端产出提取 ──────────────────────────────────────────────────

def _extract_pymupdf_chain(file_bytes: bytes) -> str | None:
    """D-17 之前的既有降级链（pymupdf4llm→pymupdf），独立于 docling 重新实现，
    作为 A/B 基线（不经过 `extract_text_from_pdf` 的 docling 前段）。刻意与
    `regulation_importer.extract_text_from_pdf` 里的原始降级分支保持逐行一致，
    任何一处后续演化都需要同步检查两处是否仍语义等价。
    """
    try:
        import fitz
        import pymupdf4llm  # type: ignore

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        return pymupdf4llm.to_markdown(doc)
    except ImportError:
        pass

    try:
        import fitz

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        pages = [page.get_text() for page in doc]
        return "\n\n".join(pages)
    except Exception:  # noqa: BLE001 — 评测脚本容错，样本记为不可用而非崩溃
        return None


# ── 金标签 / 样本 ─────────────────────────────────────────────────

@dataclass(frozen=True)
class ParseGoldLabels:
    """单份规范 PDF 的金标签。"""
    article_nos: tuple[str, ...] = ()  # 期望抽到的条文编号，顺序即金标准阅读顺序
    table_expected: bool = False       # 该样本是否含需保真的表格

    def to_dict(self) -> dict:
        return {"article_nos": list(self.article_nos), "table_expected": self.table_expected}


@dataclass(frozen=True)
class ParseEvalSample:
    file_bytes: bytes
    filename: str = "regulation.pdf"
    gold: ParseGoldLabels = field(default_factory=ParseGoldLabels)
    sample_id: str = ""


# ── 度量 ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SetMetrics:
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float

    def to_dict(self) -> dict:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def extract_article_numbers(text: str) -> list[str]:
    """复用既有 `split_into_paragraphs` 分段口径，从每段首行抠条文编号
    （与 `regulation_importer.local_extract_article` 的编号抽取正则同源），
    保留出现顺序供顺序保真度量使用。
    """
    numbers: list[str] = []
    for para in split_into_paragraphs(text):
        first_line = para["text"].splitlines()[0] if para["text"] else ""
        match = _ARTICLE_NO_PREFIX.match(first_line)
        if match:
            numbers.append(match.group(1))
    return numbers


def match_article_numbers(extracted: list[str], gold: list[str]) -> SetMetrics:
    """条文编号多重集精确匹配（允许同编号重复出现各自计数，一般不应重复但
    若后端误抽重复不应被静默去重掩盖）。"""
    remaining = list(gold)
    tp = 0
    for number in extracted:
        if number in remaining:
            remaining.remove(number)
            tp += 1
    fp = len(extracted) - tp
    fn = len(remaining)
    precision, recall, f1 = _prf(tp, fp, fn)
    return SetMetrics(tp, fp, fn, precision, recall, f1)


def _lcs_length(a: list[str], b: list[str]) -> int:
    """标准 O(len(a)*len(b)) 最长公共子序列长度，输入规模是条文编号量级
    （几十到几百），无需优化。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        curr = [0] * (len(b) + 1)
        for j, y in enumerate(b, start=1):
            curr[j] = prev[j - 1] + 1 if x == y else max(prev[j], curr[j - 1])
        prev = curr
    return prev[-1]


def order_fidelity(extracted: list[str], gold: list[str]) -> float | None:
    """抽取序列相对金标准序列的顺序保真度 = LCS(命中子序列, 金标准) / len(金标准)。

    先过滤掉抽取结果里不在金标准集合中的编号（噪声不应污染"顺序对不对"这个
    判断，噪声本身已经在 `match_article_numbers` 的 FP 里计过账），再算 LCS——
    这样才能纯粹回答"抽到的那些编号，彼此的相对先后顺序对不对"，专门捕捉
    F1 覆盖不到的失效模式：多栏排版把正文左右两栏交错拼接，编号都"抽到了"
    但顺序错乱，下游按顺序拼接条文正文时会把不相邻的两段接在一起。

    gold 为空或抽取结果与金标准无交集时返回 None（不可判定，不用 0.0 冒充
    "顺序全错"——真实原因可能是本来就没抽到任何有效编号，属于召回率问题
    而非顺序问题，两者不应混为一谈）。
    """
    if not gold:
        return None
    matched_seq = [n for n in extracted if n in gold]
    if not matched_seq:
        return None
    return _lcs_length(matched_seq, list(gold)) / len(gold)


def detect_table_separator_rows(text: str) -> int:
    """统计 Markdown 表格分隔行数量（``| --- | --- |`` 形式），作为"表格是否
    被保留为结构化表格语法"的确定性代理指标。非语义级表格内容比对，见模块
    docstring"已知局限"。
    """
    return sum(1 for line in text.splitlines() if "|" in line and _TABLE_SEPARATOR_ROW.match(line))


@dataclass(frozen=True)
class SampleParseMetrics:
    article: SetMetrics
    order_fidelity: float | None
    table_detected: bool
    table_expected: bool


def evaluate_parse(text: str, gold: ParseGoldLabels) -> SampleParseMetrics:
    extracted = extract_article_numbers(text)
    article_metrics = match_article_numbers(extracted, list(gold.article_nos))
    fidelity = order_fidelity(extracted, list(gold.article_nos))
    table_detected = detect_table_separator_rows(text) > 0
    return SampleParseMetrics(
        article=article_metrics,
        order_fidelity=fidelity,
        table_detected=table_detected,
        table_expected=gold.table_expected,
    )


# ── 多样本聚合 + 报告 ────────────────────────────────────────────

@dataclass(frozen=True)
class BackendParseReport:
    backend_name: str
    article: SetMetrics
    table: SetMetrics
    mean_order_fidelity: float | None
    sample_count: int
    unavailable_samples: int

    def to_dict(self) -> dict:
        return {
            "backend_name": self.backend_name,
            "article": self.article.to_dict(),
            "table": self.table.to_dict(),
            "mean_order_fidelity": (
                round(self.mean_order_fidelity, 4) if self.mean_order_fidelity is not None else None
            ),
            "sample_count": self.sample_count,
            "unavailable_samples": self.unavailable_samples,
        }


@dataclass(frozen=True)
class ParseComparisonReport:
    backends: dict[str, BackendParseReport] = field(default_factory=dict)
    sample_count: int = 0

    def to_dict(self) -> dict:
        return {
            "sample_count": self.sample_count,
            "backends": {k: v.to_dict() for k, v in self.backends.items()},
        }


def _sum_set_metrics(items: list[SetMetrics]) -> SetMetrics:
    tp = sum(m.tp for m in items)
    fp = sum(m.fp for m in items)
    fn = sum(m.fn for m in items)
    precision, recall, f1 = _prf(tp, fp, fn)
    return SetMetrics(tp, fp, fn, precision, recall, f1)


def run_backend_comparison(
    samples: list[ParseEvalSample],
    extractors: dict[str, object],
) -> ParseComparisonReport:
    """``extractors``: {显示名: callable(file_bytes, filename) -> str | None}。"""
    results: dict[str, BackendParseReport] = {}
    for name, extractor in extractors.items():
        article_parts: list[SetMetrics] = []
        table_binary: list[SetMetrics] = []
        fidelity_values: list[float] = []
        unavailable = 0

        for sample in samples:
            text = extractor(sample.file_bytes, sample.filename)
            if not text:
                unavailable += 1
                # 不可用时金标签全部计为 FN（如实反映"这个后端在这份规范上
                # 压根没跑起来"，与 D-16 OCR 评测基座同一处理方式）
                article_parts.append(
                    SetMetrics(0, 0, len(sample.gold.article_nos), 0.0, 0.0, 0.0)
                )
                if sample.gold.table_expected:
                    table_binary.append(SetMetrics(0, 0, 1, 0.0, 0.0, 0.0))
                else:
                    table_binary.append(SetMetrics(0, 0, 0, 0.0, 0.0, 0.0))
                continue

            metrics = evaluate_parse(text, sample.gold)
            article_parts.append(metrics.article)
            if metrics.order_fidelity is not None:
                fidelity_values.append(metrics.order_fidelity)

            tp = 1 if (metrics.table_expected and metrics.table_detected) else 0
            fp = 1 if (metrics.table_detected and not metrics.table_expected) else 0
            fn = 1 if (metrics.table_expected and not metrics.table_detected) else 0
            table_binary.append(SetMetrics(tp, fp, fn, *_prf(tp, fp, fn)))

        results[name] = BackendParseReport(
            backend_name=name,
            article=_sum_set_metrics(article_parts),
            table=_sum_set_metrics(table_binary),
            mean_order_fidelity=(
                sum(fidelity_values) / len(fidelity_values) if fidelity_values else None
            ),
            sample_count=len(samples),
            unavailable_samples=unavailable,
        )

    return ParseComparisonReport(backends=results, sample_count=len(samples))


def render_markdown(report: ParseComparisonReport, *, title: str = "D-17 规范解析 A/B 评测报告") -> str:
    lines: list[str] = [f"# {title}", "", f"> 样本数：{report.sample_count}", ""]
    if not report.backends:
        lines += ["（无后端参与评测）", ""]
        return "\n".join(lines)

    lines += [
        "## 条文编号抽取",
        "",
        "| 后端 | Precision | Recall | F1 | TP | FP | FN | 不可用样本 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, bm in report.backends.items():
        a = bm.article
        lines.append(
            f"| {name} | {a.precision:.3f} | {a.recall:.3f} | {a.f1:.3f} | "
            f"{a.tp} | {a.fp} | {a.fn} | {bm.unavailable_samples}/{bm.sample_count} |"
        )
    lines.append("")

    lines += ["## 条文顺序保真（LCS 占比，多栏排版是否被正确线性化）", "", "| 后端 | 均值 |", "|---|---|"]
    for name, bm in report.backends.items():
        val = f"{bm.mean_order_fidelity:.3f}" if bm.mean_order_fidelity is not None else "N/A（样本不足/无命中）"
        lines.append(f"| {name} | {val} |")
    lines.append("")

    lines += [
        "## 表格结构保真（Markdown 表格语法探测，代理指标非内容级比对）",
        "",
        "| 后端 | Precision | Recall | F1 | TP | FP | FN |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, bm in report.backends.items():
        t = bm.table
        lines.append(
            f"| {name} | {t.precision:.3f} | {t.recall:.3f} | {t.f1:.3f} | {t.tp} | {t.fp} | {t.fn} |"
        )
    lines.append("")

    usable = {
        name: bm for name, bm in report.backends.items()
        if bm.sample_count > bm.unavailable_samples
    }
    lines.append("## 结论摘要")
    lines.append("")
    if not usable:
        lines.append("- 本轮无可用后端产出预测（docling 未安装属预期，见模块 docstring）。")
    else:
        best = max(usable, key=lambda n: usable[n].article.f1)
        lines.append(f"- 条文抽取 F1 最优后端为 **{best}** ({usable[best].article.f1:.3f})。")
    lines.append("")
    lines.append(
        "> 注：unavailable 计入分母但不参与「跑了但读不准」的口径判断；"
        "docling 未安装时其行会显示 unavailable=sample_count/sample_count，"
        "这是预期结果，不代表 docling 效果差，只代表本轮环境未装。"
    )
    lines.append("")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────

def _demo_samples() -> list[ParseEvalSample]:
    """合成 demo：不依赖真实 PDF/docling，验证基座端到端可跑。"""
    good_text = (
        "4.2.1 消防车道净宽度\n不应小于4米。\n\n"
        "4.2.2 消防车道净空高度\n不应小于4米。\n\n"
        "4.2.3 消防车道转弯半径\n应满足消防车最小转弯半径要求。\n\n"
        "| 参数 | 数值 |\n| --- | --- |\n| 净宽 | 4m |\n"
    )
    scrambled_text = (
        "4.2.1 消防车道净宽度\n不应小于4米。\n\n"
        "4.2.3 消防车道转弯半径\n应满足消防车最小转弯半径要求。\n\n"
        "4.2.2 消防车道净空高度\n不应小于4米。\n\n"
    )
    gold = ParseGoldLabels(article_nos=("4.2.1", "4.2.2", "4.2.3"), table_expected=True)
    samples = [
        ParseEvalSample(file_bytes=good_text.encode("utf-8"), sample_id="demo-good", gold=gold),
        ParseEvalSample(file_bytes=scrambled_text.encode("utf-8"), sample_id="demo-scrambled", gold=gold),
    ]

    def _decode(file_bytes: bytes, _filename: str = "") -> str:
        return file_bytes.decode("utf-8")

    def _decode_drop_table(file_bytes: bytes, _filename: str = "") -> str:
        text = file_bytes.decode("utf-8")
        return "\n".join(line for line in text.splitlines() if "|" not in line)

    extractors = {
        "docling(demo:结构保真)": _decode,
        "pymupdf4llm_chain(demo:丢表格)": _decode_drop_table,
    }
    return samples, extractors


def _load_manifest(path: Path) -> list[ParseEvalSample]:
    data = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    samples: list[ParseEvalSample] = []
    for s in data.get("samples", []):
        pdf_path = Path(s["path"])
        if not pdf_path.is_absolute():
            pdf_path = base / pdf_path
        g = s.get("gold", {})
        gold = ParseGoldLabels(
            article_nos=tuple(str(v) for v in g.get("article_nos", [])),
            table_expected=bool(g.get("table_expected", False)),
        )
        samples.append(
            ParseEvalSample(
                file_bytes=pdf_path.read_bytes(),
                filename=pdf_path.name,
                gold=gold,
                sample_id=str(s.get("sample_id", pdf_path.name)),
            )
        )
    return samples


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="D-17 规范解析 A/B 评测基座")
    p.add_argument("--manifest", default=None, help="评测清单 JSON")
    p.add_argument("--demo", action="store_true", help="合成 demo（无需真实数据/docling，验证基座端到端）")
    p.add_argument("--out", default=None, help="Markdown 报告输出路径")
    p.add_argument("--json", default=None, help="指标 JSON 输出路径")
    args = p.parse_args(argv)

    if args.demo:
        samples, extractors = _demo_samples()
    elif args.manifest:
        mpath = Path(args.manifest)
        if not mpath.exists():
            print(f"错误：清单不存在 {mpath}", file=sys.stderr)
            return 2
        samples = _load_manifest(mpath)
        extractors = {
            "docling": extract_with_docling,
            "pymupdf4llm_chain": _extract_pymupdf_chain,
            "current_pipeline": current_pipeline_extract,
        }
    else:
        print("需 --demo 或 --manifest", file=sys.stderr)
        return 2

    report = run_backend_comparison(samples, extractors)
    md = render_markdown(report)

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"报告 → {args.out}")
    else:
        print(md)
    if args.json:
        Path(args.json).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"指标 JSON → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
