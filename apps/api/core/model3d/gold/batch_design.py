"""金标准批次设计 —— 把已知的三条教训焊进生成器，不靠每次记得。

## 教训①：裁图必须按原生分辨率渲染到格子大小

比例批把 1872×1324 的裁图缩到 420px 格子，判据要用的门（1m）只剩 7px，
判读者要用的参照物根本看不见 —— 真信号与伪影因此混在一起，事后分不开。
旧的构件批生成器用固定 `DPI=150` 渲染后再缩进格子，是同一个结构。

改法：**先定裁框（页面点），再按「裁框点数 → 格子像素」反算 DPI**，
只渲染裁框那一块（`get_pixmap(clip=…)`）。裁图出来就是格子大小，
一个像素都不缩放。代价只有裁框那一块的渲染，不是整页。

## 教训②：分层抽样必然放大稀有类

按工程×专业分层、每层取定额，是为了让稀有层也有样本；但它同时让
「样本里的占比」偏离「语料里的占比」。所以分层之后**必须加权回语料**
再报精确率（见 `validity.weighted_precision`），并报覆盖率。

## 判据从 CRITERIA.md 程序化抽取

`CRITERIA.md` 规定「每份批次命令的判据段落必须从本文件抄」。
手抄出过两次不可比（柱 0.59 vs 0.22、68% vs 22%），第二次还是在立了
规矩之后 —— **建了规矩不等于守了规矩**。所以这里改成程序抽取，
找不到小节就报错，绝不静默发出一份没有判据的批次。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Candidate:
    """一个待判读的候选（一格）。"""
    stratum: str
    drawing_id: str
    key: str
    payload: dict = field(default_factory=dict, compare=False, hash=False)


# ── 裁框与渲染分辨率 ──────────────────────────────────────────────

#: 裁框至少这么多页面点 —— 比例错到离谱时（全库只有 68.6% 的系统比例
#: 与图上印刷值一致），「5 米」换算出的点数可能缩成一个点，要有底。
MIN_CROP_PT = 40.0


def crop_box_pt(
    elem_box: tuple[float, float, float, float],
    scale_m_pt: float,
    *,
    page_w: float,
    page_h: float,
    ctx_m: float = 5.0,
    rel: float = 4.0,
    min_pt: float = MIN_CROP_PT,
) -> tuple[float, float, float, float]:
    """候选的裁框（页面点，正方形，贴边时整体平移回页内）。

    边长取三者最大：构件尺寸 × `rel`（看得清构件本身）、`ctx_m` 换算的点数
    （看得见周围的轴线交点与编号 —— 判「是不是柱」离不开上下文）、`min_pt`
    （比例离谱时的兜底）。再以页面短边封顶。
    """
    x0, y0, x1, y1 = elem_box
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    elem_side = max(x1 - x0, y1 - y0, 0.0)
    ctx_pt = ctx_m / scale_m_pt if scale_m_pt and scale_m_pt > 0 else 0.0
    side = max(elem_side * rel, ctx_pt, min_pt)
    side = min(side, float(page_w), float(page_h))
    half = side / 2.0
    bx0, by0 = cx - half, cy - half
    # 伸出页面的部分渲染出来是空白，会误导判读 —— 整体平移回来
    bx0 = min(max(bx0, 0.0), float(page_w) - side)
    by0 = min(max(by0, 0.0), float(page_h) - side)
    return (bx0, by0, bx0 + side, by0 + side)


def render_dpi_for_crop(
    crop_w_pt: float,
    crop_h_pt: float,
    *,
    cell_px: int,
    max_dpi: float = 600.0,
) -> tuple[float, bool]:
    """让裁框**恰好**渲染成 `cell_px` 像素所需的 DPI；返回 (dpi, 是否封顶)。

    封顶意味着裁框太小、要放大才能填满格子 —— 调用方要把这件事记下来，
    因为那一格是插值放大的，不是原生分辨率。
    """
    side_pt = max(float(crop_w_pt), float(crop_h_pt), 1e-6)
    dpi = float(cell_px) / (side_pt / 72.0)
    if dpi > max_dpi:
        return max_dpi, True
    return dpi, False


# ── 分层抽样 ─────────────────────────────────────────────────────

def stratify(
    pool: list[Candidate],
    *,
    per_stratum: int,
    per_drawing: int,
    seed: int,
) -> list[Candidate]:
    """每层至多取 `per_stratum` 格、每张图至多 `per_drawing` 格。

    稀有层有多少取多少，不因为凑不满配额就丢掉 —— 它们正是分层要保住的。
    每张图的上限防的是 YOLO 批出过的「一张图占 15 格」的集中。
    同一个 `seed` 必得同一批（可复现）。
    """
    rng = random.Random(seed)
    by_stratum: dict[str, list[Candidate]] = {}
    for cand in pool:
        by_stratum.setdefault(cand.stratum, []).append(cand)

    picked: list[Candidate] = []
    for stratum in sorted(by_stratum):
        cands = sorted(by_stratum[stratum], key=lambda c: c.key)
        rng.shuffle(cands)
        used: dict[str, int] = {}
        taken = 0
        for cand in cands:
            if taken >= per_stratum:
                break
            if used.get(cand.drawing_id, 0) >= per_drawing:
                continue
            used[cand.drawing_id] = used.get(cand.drawing_id, 0) + 1
            picked.append(cand)
            taken += 1
    return picked


# ── 批内重测对 ───────────────────────────────────────────────────

def plan_duplicates(
    keys: list[str],
    *,
    sheet_of: dict[str, int],
    fraction: float,
    n_sheets: int,
    seed: int,
) -> list[tuple[str, int]]:
    """挑出要重复出现的格子，并为每份副本选一个**不同的页**。

    同一页上并排出现，判读者会照抄自己，测出来的是记忆不是信度。
    返回 [(原格 key, 副本所在页)]。只有一页时无从错开，返回空。
    """
    if n_sheets < 2 or not keys:
        return []
    rng = random.Random(seed)
    k = max(1, round(len(keys) * fraction))
    originals = rng.sample(sorted(keys), min(k, len(keys)))
    plan: list[tuple[str, int]] = []
    for key in originals:
        choices = [s for s in range(n_sheets) if s != sheet_of.get(key)]
        plan.append((key, rng.choice(choices)))
    return plan


# ── 判据：从 CRITERIA.md 按小节抽取 ───────────────────────────────

_HEADING = re.compile(r"^##\s+(.*)$")


def _heading_keys(title: str) -> list[str]:
    """`## columns / column_outline —— 什么算「柱」` → ['columns', 'column_outline']"""
    head = re.split(r"——|—|--", title, maxsplit=1)[0]
    return [part.strip() for part in head.split("/") if part.strip()]


def criteria_section(path: Path | str, key: str) -> str:
    """取 `CRITERIA.md` 中 `key` 那一节的原文（含标题，到下一个 `##` 为止）。

    找不到就抛 `KeyError` —— 绝不静默发出一份没有判据的批次。
    同名小节出现多次时取第一处（`pipes` 有正文与「见对应 json」两处）。
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    start = None
    for i, line in enumerate(lines):
        match = _HEADING.match(line)
        if not match:
            continue
        if start is not None:
            return "\n".join(lines[start:i]).rstrip() + "\n"
        if key in _heading_keys(match.group(1)):
            start = i
    if start is not None:
        return "\n".join(lines[start:]).rstrip() + "\n"
    raise KeyError(f"CRITERIA.md 里没有「{key}」这一节")
