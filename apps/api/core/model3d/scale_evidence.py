"""比例可信度的**多来源交叉证据**（工作包 B）。

## 为什么要有这个模块

`drawing_transform.confidence` 的旧公式是

    confidence = (带标签轴线数 / 轴线总数) × (比例是否常用值 ? 1.0 : 0.5)

它衡量的是**轴号识别质量 × 比例是不是个常用数**，
**与比例对不对无关**。金标准 `data/model3d/gold/drawing_scale_v1.json`
的 56 条独立裁决实测：

    confidence = 1.00  →  合理率 24%
    confidence < 1.00  →  合理率 37%

**置信度携带的是负信息。** 梁批（`beams_retest_v1.json`）独立同向佐证：
有「满分置信」变换记录的图梁精确率 44%，无记录的 78%（n=16 vs 18）。

本模块给出一个**真正衡量比例对错**的分数：把几条互相独立、
各有国标出处的证据摆出来，逐条给分、逐条记录是否可得，加权合成。

## 采用的证据（附实测依据）

### 1. `printed_scale` —— 图上印刷的 `1:N`（权重 0.70，最强）

GB/T 50001 §6.0.4 要求图样注明所用比例；实际工程图写在**图框栏**里，
或紧跟在视图名称后面（「一层平面图 1:100」）。

**可得性实测**（金标准那 60 张图）：

| 来源 | 有 `1:N` 的图 |
|---|---|
| PDF 矢量文字（`geom.texts`）| **5/56，其中 4 条是误命中**（`1:5`/`1:8`/`1:2` 来自别的数字）|
| 图纸信息档案 OCR（`drawing_extracted_info`）| **54/60** |

也就是说 `element_recognizer._detect_scale` 的「读图上明写比例」那条路
**在这批图上几乎从来没走通过** —— 这批 PDF 的文字是轮廓化的。
真正的比例来源退化成了两个猜测：`8.4 米 / 轴距中位数`，
以及缺省值 `1:100`。**这才是比例只有 30% 站得住的直接原因。**

所以本模块把文本来源做成**可注入**的：调用方能把档案 OCR 文本传进来
（`texts=`），传不进来才退回矢量文字。接线点在
`services/drawing_info_extractor.py`（属于另一个工作包的文件，尚未接）。

**准确性的独立验证**：对两张「系统值 = 印刷值 = 1:150」而金标准判为
`too_short` 的图，按原分辨率（1889×1340，不缩到 420）重画 8 米红线后
逐一核对 —— `Y4CX` 图上标注链 3300mm 实测约 100 像素、8 米红线 250 像素，
比值 3.3m:8m 吻合到 3%；`REAV` 卫生间隔间按 1:150 换算 1.2 米、
盥洗盆 0.96 米，均在常规尺寸内（若真如判读所言是 1:100，
隔间只有 0.81 米，不成立）。**印刷比例是对的，那两条金标准裁决是错的**，
原因见文末「已知局限」。

### 2. `standard_denominator` —— 分母是不是 §6.0.4 表里的值（权重 0.10，弱）

**必要不充分**，而且实测证明它**不能给大权重**：金标准四个分层里，
「满分置信·**常用比例**」这一层的合理率**最低**（14.3%，n=14），
比「中等置信」（40%，n=15）还差。旧公式正是把这条当成了半个置信度。

比例表取自 `services.scale_candidates.STANDARD_DENOMINATORS`
（已按 GB/T 50001 原件校订过，删掉了凭记忆写进去的 `1:75`）。

### 3. `sheet_coverage` —— 图幅 × 比例 = 这张纸能画下多少米（权重 0.20）

边界从国标推出来，**不是回测拟合的**：
A4 短边 210mm × 1:1 = 0.21 米；A0 长边 1189mm × 1:2000 = 2378 米。
超出这个区间的比例不用看图就知道错 —— 实测 `NWPV` 覆盖 5829 米、
`WMY4` 覆盖 0.68 米（一张柱桩平面图）。

## 量过但**没有采用**的证据（记下来，免得下一个人再走一遍）

判据：在 56 条金标准裁决上算 AUC（0.5 = 无信号）。

| 候选 | AUC | 结论 |
|---|---|---|
| 线对间距落在墙厚窗口 0.1~0.5 m 的占比 | 0.42~0.47 | **无信号**，且限定在判读那一格里更差（0.37）|
| 线长中位数/分位数落在建筑尺度窗口 | 0.41~0.45 | 无信号 |
| 模数吸附度（GB/T 50002 基本模数 100mm）| 0.36~0.46 | 无信号；矢量坐标噪声与截断把节奏抹平了 |
| 轴距中位数落在 3~12 m | 0.56（n=19）| **循环论证**：`_detect_scale` 在读不到比例文字时就是用 `8.4/轴距` 反推的，于是轴距换算回去必然是 8.4 米。实测 16 张有轴距的图里 8 张正好是 8.400 |
| 尺寸链相邻标注中心距 | 与印刷比例只对上 1/23 | 与 `scale_candidates` 记的 3.6% 同向；OCR 标注的邻接关系恢复不出来 |

另有一条**结构性障碍**：`geometry_extractor.MAX_PRIMITIVES = 20000`，
这批图基本每张都被截断（`n_lines` 普遍 19000~20000），
所以任何基于「整张图的几何统计量」的证据都建立在一个任意前缀上。

## 已知局限（必须一起读）

1. **一张图上可能有多个比例。** 平面图 1:100 配角落里 1:10 的节点详图是
   常态。本模块给的是**整张图的主比例**（图框带优先），对详图区域不成立。
2. **金标准的 56 条裁决与本模块的分数呈负相关** —— 纯证据分 AUC **0.360**、
   接进 `_blend_confidence` 后 **0.431**，旧 confidence 是 0.442
   （0.5 = 无信号，越低越是反着来）。**验收指标「与 56 条正相关」没达到。**
   已定位到原因，不是本模块的缺陷，见 `docs/` 中工作包 B 的回测报告：
   判读接触表把 1889×1340 的裁图缩到 **420 像素**再给判读者，
   而判据要求拿门（1m）、楼梯踏步（0.3m）去比 —— 在一张正确的 1:150
   A0 平面图上，缩完之后门只剩 **7 像素**、踏步 **2 像素**，
   参照物根本看不见。实测判读结果对「裁格覆盖多少米」的依赖
   AUC 达 **0.68**，合理率在覆盖 6~12 米时最高（75%），
   之后随覆盖变大单调下降到 0%。
   **在「系统值 = 图上印刷值」的 21 张图里，金标准判「不合理」的有 18 张
   （合理率 14.3%）；而「系统值 ≠ 印刷值」的 29 张反而有 44.8% 被判合理。**
   复算方式见 `scripts/model3d/backtest_scale_evidence.py`，
   编号→图纸的对照表由 `scripts/model3d/scale_gold_manifest.py` 复原
   （56/56 逐条与金标准 note 里的 scale/confidence 对上）。
3. 因此**不要**用这个分数去复现金标准的 30%。要复现它，就得把
   「裁格覆盖 ≈ 10 米」编进分数里 —— 那是在编码仪器的分辨率缺陷。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from services.scale_candidates import STANDARD_DENOMINATORS

#: 1 排版点 = 25.4/72 mm。比例分母 ↔ `scale_m_pt` 的换算基准（物理关系精确）。
PT_TO_MM = 25.4 / 72

#: 图上印刷比例的正则。全角冒号与空格都要吃 —— 实测档案里
#: `1: 150`、`1：10`、`平面图1:100` 三种写法都有。
_PRINTED_RE = re.compile(r"1\s*[:：]\s*(\d{1,7})")

#: 印刷比例与实测比例的相对容差。取 10%：§6.0.4 表里相邻两档最密处是
#: 1:5→1:6 与 1:50→1:60（都差 20%），10% 不会跨到相邻档。
#: 与 `drawing_transform.SNAP_TOLERANCE` 同源同值。
PRINTED_TOLERANCE = 0.10

#: 图框带（比例尺的主位置）。与 `services.scale_candidates.in_title_block`
#: 同源判据：常规施工图的图框在**右侧或下侧**。
TITLE_BLOCK_RIGHT_RATIO = 0.72
TITLE_BLOCK_BOTTOM_RATIO = 0.80
#: 图框带内候选的票权倍数（沿用 `scale_candidates.TITLE_BLOCK_WEIGHT`）。
TITLE_BLOCK_WEIGHT = 5

#: 图幅 × 比例的物理可能区间（米）。
#: 下限 = A4 短边 210mm × 1:1；上限 = A0 长边 1189mm × 1:2000（§6.0.4 表两端）。
#: **从国标推出来，不是回测拟合出来的** —— 拟合出来的边界会把仪器的缺陷
#: 一起固化进去。
MIN_SHEET_COVERAGE_M = 0.21
MAX_SHEET_COVERAGE_M = 2378.0

#: 各条证据的权重。印刷比例一条要压过其余两条之和 ——
#: 实测「满分置信·常用比例」分层最差（14.3%），说明把弱证据堆到能翻盘的
#: 程度，就会重演旧公式的病。
WEIGHT_PRINTED = 0.70
WEIGHT_STANDARD = 0.10
WEIGHT_COVERAGE = 0.20

#: **只能佐证、不能独立成立**的证据。
#:
#: 「分母是 §6.0.4 表里的值」是**必要不充分**条件 —— 1:100 既可能是读对了，
#: 也可能是 `_detect_scale` 的缺省值（`_DEFAULT_SCALE` 正是 1:100）。
#: 旧公式的病就是满足一个必要条件就发满分；实测「满分置信·常用比例」
#: 分层合理率 14.3%，四层里最低。所以只剩它一条时**不给分数**。
SUPPORTING_ONLY = frozenset({"standard_denominator"})

#: **能独立确立可信度**的证据 —— 只有图纸自己写下的比例算数。
#:
#: 另外两条都是**必要不充分**条件：分母在表里、图幅覆盖物理可能，
#: 满足了也说明不了比例是对的（`_DEFAULT_SCALE` 就是 1:100，两条都满足）。
#: 所以它们只能**往下压**分数，不能把分数抬上去 ——
#: 接线见 `services.drawing_transform.transform_from_geometry`。
#:
#: **不这么分会出事**：全库 2142 条变换在没有 OCR 文本注入时只剩
#: 「图幅覆盖」一条，实测 1866 条会拿到 1.00，
#: `scale_gate` 判为可信的从 1290 涨到 1891 —— 比旧公式**更糟**。
INDEPENDENT = frozenset({"printed_scale"})


@dataclass(frozen=True)
class Evidence:
    """一条证据。`available=False` 时 `score` 无意义，且**不参与加权**。"""

    name: str
    available: bool
    score: float
    weight: float
    detail: str


@dataclass(frozen=True)
class ScaleEvidence:
    """比例证据汇总。

    `score` 是**可得证据**上的加权均值；一条都没有时为 ``None``
    —— 不是 0（那等于「已证伪」），也不是 0.5（那等于「半可信」），
    两者都是在没有信息时假装有信息。
    """

    score: float | None
    items: tuple[Evidence, ...]
    available_weight: float

    @property
    def has_independent(self) -> bool:
        """有没有**能独立确立可信度**的证据（见 `INDEPENDENT`）。

        为 False 时 `score` 只说明「没查出毛病」，不说明「是对的」——
        调用方据此决定是只往下压，还是可以整体采信。
        """
        return any(e.available and e.name in INDEPENDENT for e in self.items)

    def reason(self) -> str:
        """逐条列出证据 —— **降级必须可见**，分数不可解释就没法被质疑。"""
        return "; ".join(
            f"{e.name}={'n/a' if not e.available else f'{e.score:.2f}'}({e.detail})"
            for e in self.items
        )


def scale_denominator(scale_m_pt: float) -> float:
    """`scale_m_pt` → 图纸比例分母（1:N 里的 N）。"""
    return float(scale_m_pt) * 1000.0 / PT_TO_MM


def printed_denominators(
    texts: Iterable[Any] | None,
    *,
    page_w: float | None = None,
    page_h: float | None = None,
) -> Counter:
    """图面文本 → 印刷比例分母的票数。

    ``texts`` 的元素形如 ``(x, y, content)``（与 `DrawingGeometry.texts`
    同形），也接受只有 ``content`` 的字符串。给了 ``page_w/page_h`` 时，
    落在图框带里的票按 `TITLE_BLOCK_WEIGHT` 加权 —— 一张图上常有多个
    `1:N`，不分位置直接数票会让角落里几个详图比例压掉主比例。

    只保留 §6.0.4 表里的分母：实测档案里 `i=1:8`（坡度）、`1:0`（OCR 噪声）
    这类命中不过滤就会被当成比例尺。
    """
    votes: Counter = Counter()
    for item in texts or ():
        x, y, content = _unpack_text(item)
        if not content:
            continue
        weight = 1
        if _in_title_block(x, y, page_w, page_h):
            weight = TITLE_BLOCK_WEIGHT
        for match in _PRINTED_RE.finditer(content):
            denominator = int(match.group(1))
            if denominator in STANDARD_DENOMINATORS:
                votes[denominator] += weight
    return votes


def evaluate(
    scale_m_pt: float | None,
    *,
    texts: Iterable[Any] | None = None,
    page_w_pt: float | None = None,
    page_h_pt: float | None = None,
    printed_votes: Counter | None = None,
) -> ScaleEvidence:
    """给一个比例打**证据分**（0~1，越高越有旁证支持）。

    ``printed_votes`` 可直接注入（例如从档案 OCR 统计好的票），
    省得把整份文本传进来；不给才从 ``texts`` 现算。
    """
    denominator = _valid_denominator(scale_m_pt)
    if denominator is None:
        return ScaleEvidence(None, (), 0.0)

    votes = printed_votes if printed_votes is not None else printed_denominators(
        texts, page_w=page_w_pt, page_h=page_h_pt)
    items = (
        _printed_evidence(denominator, votes),
        _standard_evidence(denominator),
        _coverage_evidence(denominator, page_w_pt, page_h_pt),
    )
    available = [e for e in items if e.available]
    total = sum(e.weight for e in available)
    standalone = [e for e in available if e.name not in SUPPORTING_ONLY]
    if total <= 0 or not standalone:
        # 一条独立证据都没有 —— 只有「必要不充分」的佐证时也算没有。
        return ScaleEvidence(None, items, round(total, 4))
    score = sum(e.score * e.weight for e in available) / total
    return ScaleEvidence(round(min(1.0, max(0.0, score)), 4), items, round(total, 4))


# ── 各条证据 ──────────────────────────────────────────────────────

def _printed_evidence(denominator: float, votes: Counter) -> Evidence:
    if not votes:
        return Evidence("printed_scale", False, 0.0, WEIGHT_PRINTED,
                        "图上没读到 1:N")
    best = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    if abs(best - denominator) <= PRINTED_TOLERANCE * best:
        return Evidence("printed_scale", True, 1.0, WEIGHT_PRINTED,
                        f"图上写 1:{best}，一致")
    # 差一个数量级以上是**强反证**（多半是缺省值或轴距猜测），
    # 差一档之内给部分分 —— 可能是同一张图上的另一个视图比例。
    ratio = max(best, denominator) / max(min(best, denominator), 1e-9)
    partial = 0.35 if ratio < 3.0 else 0.0
    return Evidence("printed_scale", True, partial, WEIGHT_PRINTED,
                    f"图上写 1:{best}，实测 1:{denominator:.0f}，不一致")


def _standard_evidence(denominator: float) -> Evidence:
    hit = any(abs(denominator - d) <= 0.02 * d for d in STANDARD_DENOMINATORS)
    return Evidence("standard_denominator", True, 1.0 if hit else 0.0,
                    WEIGHT_STANDARD,
                    f"1:{denominator:.0f} {'在' if hit else '不在'} §6.0.4 表内")


def _coverage_evidence(denominator: float, page_w_pt: float | None,
                       page_h_pt: float | None) -> Evidence:
    longest = max(float(page_w_pt or 0.0), float(page_h_pt or 0.0))
    if longest <= 0:
        return Evidence("sheet_coverage", False, 0.0, WEIGHT_COVERAGE, "图幅未知")
    coverage = longest * denominator * PT_TO_MM / 1000.0
    return Evidence("sheet_coverage", True, _log_band(coverage), WEIGHT_COVERAGE,
                    f"图幅覆盖 {coverage:.1f} 米")


def _log_band(value: float) -> float:
    """落在物理可能区间内给 1，外面按十进制数量级线性衰减到 0。"""
    if value <= 0:
        return 0.0
    if MIN_SHEET_COVERAGE_M <= value <= MAX_SHEET_COVERAGE_M:
        return 1.0
    decades = (math.log10(MIN_SHEET_COVERAGE_M / value) if value < MIN_SHEET_COVERAGE_M
               else math.log10(value / MAX_SHEET_COVERAGE_M))
    return max(0.0, 1.0 - decades)


# ── 小工具 ────────────────────────────────────────────────────────

def _valid_denominator(scale_m_pt: float | None) -> float | None:
    try:
        scale = float(scale_m_pt)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(scale) or scale <= 0:
        return None
    return scale_denominator(scale)


def _unpack_text(item: Any) -> tuple[float | None, float | None, str]:
    """``(x, y, content)`` / ``content`` 两种形状都吃；认不出就当没坐标。"""
    if isinstance(item, str):
        return None, None, item
    if isinstance(item, Sequence) and len(item) >= 3:
        try:
            return float(item[0]), float(item[1]), str(item[2])
        except (TypeError, ValueError):
            return None, None, str(item[2])
    return None, None, ""


def _in_title_block(x: float | None, y: float | None,
                    page_w: float | None, page_h: float | None) -> bool:
    if not page_w or not page_h or x is None or y is None:
        return False
    return (x > page_w * TITLE_BLOCK_RIGHT_RATIO
            or y > page_h * TITLE_BLOCK_BOTTOM_RATIO)
