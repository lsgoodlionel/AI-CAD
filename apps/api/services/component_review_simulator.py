"""人审飞轮**模拟器**(方向3)——在真人审入场前,用可验证证据规则代跑闭环。

**诚实声明**:这是**模拟人审**,不是真人核对图纸。用途是:
① 验证人审闭环端到端可用(队列→决策→review_state→埋点→COCO 金标签);
② 展示飞轮效果曲线(confirmed 率、待人审下降);
③ 给真人审提供**预分诊**(证据充分的先确认,可疑的留给人重点看)。
所有决策写 `reviewer_id=SIMULATED_REVIEWER`,可一键区分/回滚,绝不冒充真人审。

**决策规则(全部基于可验证证据,非随机)**:
- confirm:完整轴网定位 + 多观测(≥2)+ 截面尺寸合理 → 多源相互印证,证据充分;
- reject:截面尺寸异常(过小/过大)→ 几何上不可能是真实构件(误检);
- 其余:**留给真人**(不做决策),这是模拟器的边界。
"""
from __future__ import annotations

from typing import Any

#: 模拟审核者标识(可据此区分/回滚模拟决策,绝不冒充真人)
SIMULATED_REVIEWER = "00000000-0000-0000-0000-0000000515ed"

#: 截面尺寸合理区间(米):超出即几何上不可能的构件
MIN_SIZE_M = 0.1
MAX_SIZE_M = 5.0
#: 证据充分所需的最少观测数
MIN_OBS_FOR_CONFIRM = 2


def _outline_size(outline: Any) -> tuple[float, float] | None:
    pts = [p for p in (outline or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
    if len(pts) < 3:
        return None
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return max(xs) - min(xs), max(ys) - min(ys)


def decide(instance: dict) -> dict:
    """单个待审构件 → 模拟决策 {action, reason} 或 {action: None}(留给真人)。

    instance 需含 grid_ref / obs_count / outline_m(或 size)。
    """
    size = _outline_size(instance.get("outline_m"))
    obs_count = int(instance.get("obs_count") or 0)
    has_full_grid = bool(instance.get("grid_ref")) and "?" not in str(instance.get("grid_ref"))

    # 规则1:几何上不可能的尺寸 → 否定(误检)
    if size is not None:
        w, h = size
        if w < MIN_SIZE_M or h < MIN_SIZE_M:
            return {"action": "reject",
                    "reason": f"截面过小({w:.2f}×{h:.2f}m),几何上不成立,判为误检"}
        if w > MAX_SIZE_M or h > MAX_SIZE_M:
            return {"action": "reject",
                    "reason": f"截面过大({w:.2f}×{h:.2f}m),超单构件合理上限,判为误检"}

    # 规则2:多源印证 + 轴网定位 → 确认
    if has_full_grid and obs_count >= MIN_OBS_FOR_CONFIRM:
        return {"action": "confirm",
                "reason": f"轴网定位 {instance.get('grid_ref')} + {obs_count} 条独立观测互证"}

    # 其余留给真人(模拟器的诚实边界)
    return {"action": None, "reason": "证据不足以自动裁决,留待真人核对"}


def simulate_batch(instances: list[dict]) -> dict:
    """批量模拟决策,返回 {decisions: [(id, action, reason)], stats}。纯函数。"""
    decisions: list[tuple[str, str, str]] = []
    stats = {"confirm": 0, "reject": 0, "deferred": 0}
    for inst in instances:
        d = decide(inst)
        if d["action"] is None:
            stats["deferred"] += 1
            continue
        decisions.append((str(inst.get("id")), d["action"], d["reason"]))
        stats[d["action"]] += 1
    return {"decisions": decisions, "stats": stats}
