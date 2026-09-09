"""图纸级准入闸：这张图该不该进构件识别。

**为什么闸开在图纸级，而不是构件级。**
1096 条独立裁决里最强的结构信号是：平均 **80%** 的图纸「全对」或「全错」
（pipes 100% · equipment 85% · columns 82% · beams 79% · walls 69% ·
slabs 62%，只统计每图 ≥2 格的单元）。松紧构件判据动不了这个分布 ——
一张图要么整张能识别，要么整张不能。另有 **24%** 的误检来自
「图纸本就无空间范围 / 无专业 / 比例错」，这些根本不该进构件识别。

**三档判定，只有一档会丢图**：

    build     照常进构件识别
    degrade   仍然进，但降级标记必须随结果走（比例不当权威、几何不归某一层……）
    skip      整页无建筑几何，丢掉

**只有一条判据够格 `skip`。** 这是实测的结论，不是保守：
`drawing_usable_v1.json` 上只有「非几何角色」做到了**误伤 0**；
其余候选判据的误伤率在 22%~55% 之间，全部降为 `degrade`。
本项目有过凭判断加阈值（`MAX_BANDS=40`）**误杀 451 张核心平面图**的事故，
所以宁可放过，不可误杀 —— 蓝图 `docs/MODELING_PIPELINE_BLUEPRINT.md` §7
约束 5：**判不出就说判不出**。

**本模块只做判定，不接线。** 接线时机由回测数字决定，见
`scripts/model3d/drawing_gate_backtest.py`。

---

## 判据清单与实测依据

| 码 | 判据 | 判定 | 实测依据（金标准文件 / 样本量 / 拦截·误伤）|
|---|---|---|---|
| `non_geometric` | 角色 = 非几何 | **skip** | `drawing_usable_v1.json` 36 张：拦 10、**误伤 0**、精确率 1.00、召回 43%。旁证 `has_discipline_v1.json` 90 张：**26% 的图根本没有专业**（目录/说明/表/封面/图例）|
| `detail` | 角色 = 详图 | degrade | `drawing_usable_v1.json`：拦 9、误伤 2 = **22%**。两张误伤（`剪力墙详图`、`楼梯ST-24、25结构详图`）落在判读者 **74% 重测信度**内，分不清是真误伤还是判读噪声 —— 不足以拦截 |
| `coordinate_base` | 角色 = 坐标基准 | degrade | 本样本 0 例；证据来自 `services/model_story.NON_FLOOR_ROLES`（同一角色集，80 张 `floor_assignment_v1.json` 上删 8/19 错误、误伤 0）|
| `elevation_reference` | 角色 = 标高来源 | degrade | 剖立面是 z 的来源不是构件来源；`axis_grid_presence_v1.json` note 实测：立面/剖面/详图按国标只出现**单方向**轴线 |
| `unknown_role` | 角色判不出 | degrade | `drawing_usable_v1.json` 上 4 张、误伤 0，**仍不升级为拦截**：4 张不足以支撑，且「判不出」不等于「不该建」（蓝图 §7 约束 5）|
| `scale_not_authoritative` | 变换不可信 | degrade | `drawing_scale_v1.json` 56 条：拦 34、误伤 12 = **35%**。比例错让尺寸整体错，但几何还在 —— 只够降级 |

## 被实测**驳回**的判据（写在这里，免得下次又有人凭直觉加回来）

* **`axis_count == 0` → 拦截**：`axis_grid_presence_v1.json` 80 条实测 ——
  系统识别 0 条轴线的 40 张里 **4 张实际有轴网（10% 误伤）**；
  即便收紧到 `axis==0 且 circle==0` 仍有 6% 误伤。
  该文件的 note 已外推：全库 `axis_count=0` 的 2045 张里约 **204 张有轴网**。
  拿它拦截就是重演 `MAX_BANDS=40`。**驳回。**
* **`discipline == 'general'` → 拦截**：`has_discipline_v1.json` 90 条 ——
  拦 20 张，其中 **11 张确实是图纸，误伤 55%**。**驳回。**
* **`suspect_symbol_field` → 拦截**：该标记的前身 `MAX_BANDS=40`
  误杀 451 张核心平面图（一层完整平面图有 42 条带），现已改为只标记不拦截。
  **不得在此处恢复其拦截语义。**
* **「比例不在 GB/T 50001 比例表上」→ 拦截**：容差 10% 时精确率 0.73，
  而「比例不合理」的基线本就是 0.70 —— **等于没有信号**。
  只有把容差放到 20%（n=4）才到 1.00，那时它已退化成「比例荒谬」，
  与 `scale_gate` 的上界重复。**驳回。**

## 一处上报给 `scale_gate` 属主的实测发现（本模块不改它）

`core/model3d/scale_gate.py` 的 `is_transform_trustworthy` 有两处在
`drawing_scale_v1.json` 56 条上不成立：

* **下界 `MIN_SCALE_DENOMINATOR = 20` 是反信号** ——
  `denom < 20` 的 18 条里精确率仅 **0.56**，而「不合理」基线是 0.70；
  换言之比例分母小于 20 的图**比平均更可能是对的**。
* **`confidence` 项是噪声** —— `conf < 0.5` 精确率 0.71、
  `conf == 1.00` 精确率 **0.76**，两侧都贴着 0.70 基线。
  这与 `drawing_scale_v1.json` note 的「置信度携带负信息」一致。
* 真正带信号的只有**上界**：`denom > 200` 的 10 条 **10/10 全部比例错**。

本模块**照旧调用** `is_transform_trustworthy`（不重写判据，避免两处漂移），
仅把上述发现记录在案。
"""
from __future__ import annotations

import collections
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from core.model3d.scale_gate import is_transform_trustworthy
from services.drawing_role import (
    ROLE_COORDINATE_BASE, ROLE_DETAIL, ROLE_ELEVATION_REFERENCE,
    ROLE_NON_GEOMETRIC, ROLE_UNKNOWN, classify_role,
)

logger = logging.getLogger(__name__)

# ── 判定档位 ────────────────────────────────────────────────────
VERDICT_BUILD = "build"
VERDICT_DEGRADE = "degrade"
VERDICT_SKIP = "skip"

#: 档位强弱。取最强的一档作为最终判定。
_VERDICT_RANK = {VERDICT_BUILD: 0, VERDICT_DEGRADE: 1, VERDICT_SKIP: 2}

# ── 判据码 ──────────────────────────────────────────────────────
CODE_NON_GEOMETRIC = "non_geometric"
CODE_DETAIL = "detail"
CODE_COORDINATE_BASE = "coordinate_base"
CODE_ELEVATION_REFERENCE = "elevation_reference"
CODE_UNKNOWN_ROLE = "unknown_role"
# ⚠ **`unknown_role` 实测是反信号，已从默认判据移除**。
# 首版在 36 张（`drawing_usable_v1`）上得到误伤 0%；用 `gold_sheets/*/manifest.tsv`
# 的真实 drawing_id 把样本扩到 295 张、并**逐判据对齐各自的真值维度**后：
#
#     判据                      可判  真该拦  误伤率   该维度基线
#     non_geometric             24     20    17%      37%   ✅ 优于基线
#     scale_not_authoritative   34     22    35%      70%   ✅ 优于基线
#     detail                     3      3     0%      24%   ⚠ n 太小
#     unknown_role              12      3    75%      37%   ❌ 劣于基线 = 反信号
#
# 36 张上的 0% 是小样本假象。见 `scripts/model3d/drawing_gate_backtest_full.py`。
CODE_SCALE_NOT_AUTHORITATIVE = "scale_not_authoritative"

#: **唯一够格拦截的角色**。实测误伤 0（`drawing_usable_v1.json` 36 张拦 10）。
SKIP_ROLES = frozenset({ROLE_NON_GEOMETRIC})

#: 降级角色 → (判据码, 说明, 实测依据)。**不拦截**，只让下游知道降级了。
_DEGRADE_ROLES: dict[str, tuple[str, str, str]] = {
    ROLE_DETAIL: (
        CODE_DETAIL,
        "节点大样：几何属于构件截面，不属于某一层",
        "drawing_usable_v1.json 36 张：拦 9、误伤 2 = 22%，"
        "落在判读者 74% 重测信度内，不足以拦截",
    ),
    ROLE_COORDINATE_BASE: (
        CODE_COORDINATE_BASE,
        "轴网定位图：用于定位，本身不贡献构件",
        "drawing_usable_v1.json 本样本 0 例；同一角色集在 "
        "floor_assignment_v1.json 80 张上删 8/19 错误、误伤 0",
    ),
    ROLE_ELEVATION_REFERENCE: (
        CODE_ELEVATION_REFERENCE,
        "剖面/立面：是楼层标高的来源，不是平面构件的来源",
        "axis_grid_presence_v1.json 80 条 note 实测：立面/剖面/详图"
        "按国标只出现单方向轴线，6/8 的「误检」实为此分类假象",
    ),
    # ROLE_UNKNOWN 曾在此降级，**已按实测移除**（见上方 CODE_UNKNOWN_ROLE 注释）：
    # 36 张上误伤 0%，扩到 295 张并对齐真值维度后误伤 75%，劣于 37% 的基线。
    # 「角色判不出」本身不携带负面信息，被它降级的图四分之三是正常图。
}

_SKIP_BASIS = (
    "drawing_usable_v1.json 36 张：拦 10、误伤 0、精确率 1.00、召回 43%；"
    "旁证 has_discipline_v1.json 90 张：26% 的图根本没有专业"
)
_SCALE_BASIS = (
    "drawing_scale_v1.json 56 条：拦 34、误伤 12 = 35%，只够降级不够拦截"
)


@dataclass(frozen=True)
class GateReason:
    """一条判据的命中记录 —— **依据链的一环**。

    `basis` 必须回指金标准文件与实测数字。没有实测依据的判据不该存在。
    """
    code: str
    verdict: str
    detail: str
    basis: str


@dataclass(frozen=True)
class DrawingSignals:
    """一张图进闸时的现有信号。**全部可缺**，缺了就降级，不阻断。"""
    drawing_id: str = ""
    drawing_no: str = ""
    title: str = ""
    discipline: str = ""
    scale_m_pt: float | None = None
    transform_confidence: float | None = None
    axis_count: int = 0
    circle_count: int = 0
    #: 调用方已算好的角色。给了就不重算，避免同一张图两处判出不同角色。
    role: str | None = None
    #: 传给 `classify_role` 的内容证据（轴号圈数、变换内点、标高链……）。
    evidence: Mapping[str, Any] | None = None
    #: 本批图纸学到的「编号段 → 角色」（`drawing_role.learn_number_patterns`）。
    number_patterns: Mapping[str, str] | None = None


@dataclass(frozen=True)
class GateResult:
    """判定结果。`reasons` 是完整依据链，**命中的每一条都留着**。"""
    drawing_id: str
    verdict: str
    role: str
    role_source: str
    reasons: tuple[GateReason, ...] = field(default_factory=tuple)

    @property
    def is_skipped(self) -> bool:
        return self.verdict == VERDICT_SKIP

    @property
    def scale_authoritative(self) -> bool:
        """这张图的比例能不能当权威。不能时下游须自行按内容估。"""
        return not any(r.code == CODE_SCALE_NOT_AUTHORITATIVE
                       for r in self.reasons)


def _coerce(signals: DrawingSignals | Mapping[str, Any]) -> DrawingSignals:
    """接受 dataclass 或普通 dict，未知字段直接忽略（边界处容错）。"""
    if isinstance(signals, DrawingSignals):
        return signals
    known = DrawingSignals.__dataclass_fields__
    return DrawingSignals(**{k: v for k, v in dict(signals).items() if k in known})


def _resolve_role(signals: DrawingSignals) -> tuple[str, str]:
    """定角色。调用方给了就用给的，否则走 `drawing_role` 三级级联。"""
    if signals.role:
        return signals.role, "given"
    result = classify_role(
        {"title": signals.title, "drawing_no": signals.drawing_no,
         "discipline": signals.discipline},
        evidence=signals.evidence,
        patterns=signals.number_patterns,
    )
    return result.role, result.source


def evaluate_drawing(
    signals: DrawingSignals | Mapping[str, Any],
) -> GateResult:
    """判一张图：`build` / `degrade` / `skip` + 依据链。

    判据之间**互不短路** —— 全部跑完，命中的都记进 `reasons`，
    最终判定取最强的一档。这样事后能追责到具体判据，
    而不是只看到一个结论。
    """
    sig = _coerce(signals)
    role, role_source = _resolve_role(sig)
    reasons: list[GateReason] = []

    # G1 非几何角色 —— 唯一够格拦截的判据
    if role in SKIP_ROLES:
        reasons.append(GateReason(
            CODE_NON_GEOMETRIC, VERDICT_SKIP,
            "说明/目录/通知单/材料表/系统图/图例：整页无建筑几何",
            _SKIP_BASIS))

    # G2~G5 判不准的角色 —— 一律降级
    degrade_role = _DEGRADE_ROLES.get(role)
    if degrade_role is not None:
        code, detail, basis = degrade_role
        reasons.append(GateReason(code, VERDICT_DEGRADE, detail, basis))

    # G6 比例不可信 —— 尺寸会整体错，但几何还在
    if not is_transform_trustworthy(sig.scale_m_pt, sig.transform_confidence):
        reasons.append(GateReason(
            CODE_SCALE_NOT_AUTHORITATIVE, VERDICT_DEGRADE,
            f"scale_m_pt={sig.scale_m_pt} confidence={sig.transform_confidence}"
            " 未通过 scale_gate，比例不当权威",
            _SCALE_BASIS))

    verdict = VERDICT_BUILD
    for reason in reasons:
        if _VERDICT_RANK[reason.verdict] > _VERDICT_RANK[verdict]:
            verdict = reason.verdict

    return GateResult(drawing_id=sig.drawing_id, verdict=verdict, role=role,
                      role_source=role_source, reasons=tuple(reasons))


def gate_drawings(
    batch: Iterable[DrawingSignals | Mapping[str, Any]],
) -> tuple[GateResult, ...]:
    """批量判定，**逐张返回、顺序不变**，并把拦截量记进日志。

    闸不静默：拦了多少、降级多少、各判据各拦了多少，都写 `logger.info`。
    """
    results = tuple(evaluate_drawing(item) for item in batch)
    if not results:
        return results
    stats = summarize(results)
    by_verdict = stats["by_verdict"]
    logger.info(
        "drawing_gate 判定 %d 张：build=%d degrade=%d skip=%d；判据命中 %s",
        stats["total"], by_verdict[VERDICT_BUILD], by_verdict[VERDICT_DEGRADE],
        by_verdict[VERDICT_SKIP], dict(stats["by_reason"]))
    return results


def summarize(results: Iterable[GateResult]) -> dict[str, Any]:
    """固定统计口径：总数 / 各档位 / 各判据命中数 / 各角色。

    口径固定是为了让接线前后的拦截量**可比** —— 本项目已因判据不固定
    出现过两次「看起来像退化、其实是口径变了」。
    """
    items = list(results)
    by_verdict = collections.Counter(
        {VERDICT_BUILD: 0, VERDICT_DEGRADE: 0, VERDICT_SKIP: 0})
    by_verdict.update(r.verdict for r in items)
    by_reason: collections.Counter[str] = collections.Counter()
    for result in items:
        by_reason.update(reason.code for reason in result.reasons)
    return {
        "total": len(items),
        "by_verdict": dict(by_verdict),
        "by_reason": dict(by_reason),
        "by_role": dict(collections.Counter(r.role for r in items)),
    }
