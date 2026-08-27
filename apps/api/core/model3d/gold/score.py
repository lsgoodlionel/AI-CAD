"""金标准评分：每种真值方法有各自的对错口径。

| method | 口径 |
|---|---|
| `count` | 绝对误差 / 相对误差 / 是否完全一致 |
| `instances` | 按**轴号身份**配对 → 精确率、召回率、漏的、多的、尺寸误差 |
| `text` | 归一化后是否相同（忽略空白与全角/半角差异） |
| `fields` | 逐字段命中 / 错值 / 缺失 |
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from .schema import ObjectClass


@dataclass
class ClassScore:
    name: str
    method: str
    exact: bool = False
    abs_error: float = 0.0
    rel_error: float = 0.0
    matched: int = 0
    missed: list = field(default_factory=list)
    spurious: list = field(default_factory=list)
    wrong: list = field(default_factory=list)
    precision: float = 0.0
    recall: float = 0.0
    #: 配上对的实体的尺寸误差（毫米）：每根取**逐边最大**，再对所有实体取平均
    size_error_mm: float | None = None

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def normalize_text(value) -> str:
    """归一化：去空白 + 全角转半角。

    图名比对不该因「（四）」与「(四)」判错 —— 那是同一个图名。
    """
    text = unicodedata.normalize("NFKC", str(value or ""))
    return "".join(text.split())


def _score_count(truth: ObjectClass, got: dict, s: ClassScore) -> ClassScore:
    actual = int(got.get("count") or 0)
    expected = int(truth.count or 0)
    s.abs_error = abs(actual - expected)
    # 真值为 0 时以 1 为分母兜底——挑空区的真值就是 0，不能算成无穷
    s.rel_error = s.abs_error / max(expected, 1)
    s.exact = actual == expected
    return s


def _score_instances(truth: ObjectClass, got: dict, s: ClassScore) -> ClassScore:
    def key(raw) -> str:
        zone = raw.get("zone")
        return f"{zone}·{raw['id']}" if zone is not None else str(raw["id"])

    want = {i.key: i for i in truth.instances}
    have = {key(i): i for i in (got.get("instances") or []) if i.get("id")}
    hit = sorted(set(want) & set(have))
    s.matched = len(hit)
    s.missed = sorted(set(want) - set(have))
    s.spurious = sorted(set(have) - set(want))
    s.precision = len(hit) / len(have) if have else 0.0
    s.recall = len(hit) / len(want) if want else 0.0
    s.exact = not s.missed and not s.spurious

    # 尺寸误差**只在配上对的实体上算** —— 没配上的谈尺寸没有意义。
    # 每根柱取**逐边最大误差**而非平均：GB 50204 卡截面尺寸是逐边卡的，
    # 一边超差就是超差；取平均会把 40 毫米的超差稀释成 20。
    errs = []
    for key in hit:
        a, b = want[key].size_mm, have[key].get("size_mm")
        if a and b:
            errs.append(max(abs(float(x) - float(y)) for x, y in zip(a, b)))
    s.size_error_mm = (sum(errs) / len(errs)) if errs else None
    return s


def _score_text(truth: ObjectClass, got: dict, s: ClassScore) -> ClassScore:
    s.exact = normalize_text(truth.text) == normalize_text(got.get("text"))
    s.abs_error = 0.0 if s.exact else 1.0
    return s


def _score_fields(truth: ObjectClass, got: dict, s: ClassScore) -> ClassScore:
    have = {k: normalize_text(v) for k, v in (got.get("fields") or {}).items()}
    for key, value in truth.fields.items():
        if key not in have:
            s.missed.append(key)
        elif have[key] == normalize_text(value):
            s.matched += 1
        else:
            s.wrong.append(key)
    s.spurious = sorted(set(have) - set(truth.fields))
    total = len(truth.fields)
    s.recall = s.matched / total if total else 0.0
    s.precision = s.matched / len(have) if have else 0.0
    s.exact = s.matched == total and not s.spurious
    return s


_SCORERS = {"count": _score_count, "instances": _score_instances,
            "text": _score_text, "fields": _score_fields}


def score_class(truth: ObjectClass, got: dict) -> ClassScore:
    """把一处范围上某一类的识别结果与真值比对。

    排除项**拒绝算分**：真值被排除是有理由的（埋件图、三方不一致），
    偷偷算进去会让指标凭空好看。调用方漏筛就该在这里炸出来。
    """
    if not truth.counts_toward_metrics:
        reason = truth.excluded or f"把握 {truth.confidence} 且未经人工复核"
        raise ValueError(f"{truth.name}: 该真值不计分（{reason}）")
    scorer = _SCORERS[truth.method]
    return scorer(truth, got or {}, ClassScore(name=truth.name, method=truth.method))
