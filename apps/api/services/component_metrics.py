"""Phase H7 验收指标 —— 量化范式收敛。纯函数 compute_metrics 可测。

诚实口径(区分真值与代理):
- vertical_reality_rate 竖向真实率 = 有真实标高(z_source∈{section/elevation/floor_elevation})占比,**真值**;
- grid_location_rate 轴网定位率 = 有轴网格 grid_ref 占比,作**位置确定性代理**
  (真·位置误差需人审金标签作真值,当前不可测,故不编造);
- review 审核收敛 = confirmed/conflict/auto 占比 + 人审动作数,**真值**;
- count_accuracy 数量准确率(可选,需构件表 BOM)= 1 - |diff|/expected,by type,**真值**。
"""
from __future__ import annotations

from typing import Any


def compute_metrics(
    summary: dict, action_counts: dict, bom: dict[str, int] | None = None,
) -> dict[str, Any]:
    """装配汇总 + 人审动作计数 (+ 可选 BOM) → 验收指标。纯函数。"""
    total = int(summary.get("total") or 0)

    def rate(n: Any) -> float:
        return round(int(n or 0) / total, 4) if total else 0.0

    metrics: dict[str, Any] = {
        "total": total,
        "vertical_reality_rate": rate(summary.get("with_z")),
        "grid_location_rate": rate(summary.get("with_grid")),   # 位置确定性代理
        "review": {
            "confirmed": int(summary.get("confirmed") or 0),
            "conflict": int(summary.get("conflict") or 0),
            "auto": int(summary.get("auto") or 0),
            "confirmed_rate": rate(summary.get("confirmed")),
        },
        "review_actions": {
            "confirm": int(action_counts.get("confirm") or 0),
            "reject": int(action_counts.get("reject") or 0),
            "reclass": int(action_counts.get("reclass") or 0),
            "total": sum(int(action_counts.get(k) or 0)
                         for k in ("confirm", "reject", "reclass")),
        },
        "by_type": summary.get("by_type") or {},
        "position_error_note": (
            "位置误差需人审金标签作真值,当前不可测;以轴网定位率(grid_location_rate)作代理"
        ),
    }
    if bom:
        from services.component_bom import reconcile_from_counts
        rec = reconcile_from_counts(summary.get("by_type") or {}, bom)
        metrics["count_accuracy"] = {
            ctype: (round(1 - abs(r["diff"]) / r["expected"], 4) if r["expected"] else None)
            for ctype, r in rec.items()
        }
    return metrics
