"""
铁三角奖金分配计算器。

分配比例硬编码，不可通过任何接口修改：
  集团（Group）   20%
  项目部（Team）  50%
  提案人           30%

奖励池 = 净节约额 × bonus_rate（默认 15%，可按项目配置）
"""
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone

# ── 铁三角（硬编码，绝不从前端接收） ───────────────────────────
_GROUP_RATIO    = Decimal("0.20")
_TEAM_RATIO     = Decimal("0.50")
_PROPOSER_RATIO = Decimal("0.30")
_DEFAULT_BONUS_RATE = Decimal("0.15")   # 净节约额的 15% 进入奖励池

# 编译期断言：三方之和必须为 100%
assert _GROUP_RATIO + _TEAM_RATIO + _PROPOSER_RATIO == Decimal("1.00"), \
    "铁三角比例之和必须为 1.00"


def _round2(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate(
    net_saving: Decimal,
    bonus_rate: Decimal = _DEFAULT_BONUS_RATE,
    calculated_by: str = "",
) -> dict:
    """
    计算铁三角分配结果，返回可直接写入 cost_snapshot 的 dict。

    Args:
        net_saving:    商务核算净节约额（元）
        bonus_rate:    奖励池比例（默认 0.15，即净节约的 15%）
        calculated_by: 计算人 user_id，用于审计

    Returns:
        dict 包含全部中间量，供写入 cost_snapshot（防争议凭证）
    """
    if net_saving <= 0:
        raise ValueError("净节约额必须大于零")
    if not (Decimal("0.01") <= bonus_rate <= Decimal("0.50")):
        raise ValueError("奖励比例须在 1%–50% 之间")

    bonus_pool      = _round2(net_saving * bonus_rate)
    group_amount    = _round2(bonus_pool * _GROUP_RATIO)
    team_pool       = _round2(bonus_pool * _TEAM_RATIO)
    proposer_amount = _round2(bonus_pool * _PROPOSER_RATIO)

    # 尾差补偿给集团（防止分配总额因舍入 ≠ bonus_pool）
    diff = bonus_pool - group_amount - team_pool - proposer_amount
    group_amount += diff

    return {
        "net_saving":       float(net_saving),
        "bonus_rate":       float(bonus_rate),
        "bonus_pool":       float(bonus_pool),
        "iron_triangle": {
            "group_ratio":    float(_GROUP_RATIO),
            "team_ratio":     float(_TEAM_RATIO),
            "proposer_ratio": float(_PROPOSER_RATIO),
        },
        "group_amount":    float(group_amount),
        "team_pool":       float(team_pool),
        "proposer_amount": float(proposer_amount),
        "calculated_at":   datetime.now(timezone.utc).isoformat(),
        "calculated_by":   calculated_by,
    }


def amounts_from_snapshot(snapshot: dict) -> tuple[Decimal, Decimal, Decimal]:
    """从 cost_snapshot 提取三方金额（用于写库）。"""
    return (
        Decimal(str(snapshot["group_amount"])),
        Decimal(str(snapshot["team_pool"])),
        Decimal(str(snapshot["proposer_amount"])),
    )
