"""层内坐标系矛盾检测 —— 出矛盾点，交人判断（用户第 3 项口径）。

> 「相对配准与绝对摆放，没有矛盾的时候直接存在，矛盾时出矛盾点，
>   提交人工判断，并为判断提供原始依据（如图纸和模型）及数据文字信息支撑」

**实测矛盾**（模型 v53）：B1 / F2 / RF 三层的构件横跨两个坐标系 ——
有世界锚点的图被 `place_elements` 摆到工程坐标（−6300 附近），
其余图仍按轴号配准留在局部系（0~200），同层跨度因此虚报 6300+ 米。

**为什么不能自动统一到世界坐标**：工程坐标系与图纸之间有旋转
（Phase I 实测 **70.29°**），而 scene 的 `axes` 是 `{x:[{coord}], y:[...]}`
的**轴对齐**结构，装不下旋转后的斜轴线。强行摆过去会毁掉轴网表达。

**所以本模块只做两件事**：

1. **判定矛盾**：该层是否既有绝对摆放的图、又有只能相对配准的图；
2. **给出依据**：各是哪几张、占比多少、两组构件中心差多远 ——
   让人能判断是「该补锚点」还是「该放弃这层的绝对摆放」。

矛盾时由调用方决定处置（当前策略：整层退回局部系，降级可见）。
**不替人做取舍**：绝对摆放是 J1 的成果，不该被自动关掉而不留痕。
"""
from __future__ import annotations

from typing import Any

#: 两组构件中心相距多远算「确有矛盾」（米）。
#:
#: 局部坐标是图幅尺度（百米量级），工程坐标在 −6300 附近，
#: 真矛盾的差值是**千米量级**；取 500 米可靠地把两者分开，
#: 又不会把同一坐标系内的正常离散误判成矛盾。
MIN_CONFLICT_DISTANCE_M = 500.0


def detect_floor_conflict(
    floor_key: str,
    placed_drawings: list[str],
    unplaced_drawings: list[str],
    placed_centre: tuple[float, float] | None = None,
    unplaced_centre: tuple[float, float] | None = None,
) -> dict[str, Any] | None:
    """该层是否存在坐标系矛盾 → 矛盾点（无矛盾返回 None）。

    只有**两类图同时存在**才谈得上矛盾：全部绝对摆放、或全部相对配准，
    坐标系都是自洽的。
    """
    if not placed_drawings or not unplaced_drawings:
        return None

    distance = None
    if placed_centre and unplaced_centre:
        distance = (
            (placed_centre[0] - unplaced_centre[0]) ** 2
            + (placed_centre[1] - unplaced_centre[1]) ** 2
        ) ** 0.5
        # 两组本就在一起 ⇒ 摆放与配准的结果一致，**没有矛盾**（用户口径：
        # 「没有矛盾的时候直接存在」），不必惊动人。
        if distance < MIN_CONFLICT_DISTANCE_M:
            return None

    total = len(placed_drawings) + len(unplaced_drawings)
    return {
        "floor": floor_key,
        "placed_count": len(placed_drawings),
        "unplaced_count": len(unplaced_drawings),
        "placed_ratio": round(len(placed_drawings) / total, 3),
        # 依据：具体是哪几张图，人要能点开对照
        "placed_drawings": list(placed_drawings),
        "unplaced_drawings": list(unplaced_drawings)[:20],
        "distance_m": round(distance, 1) if distance is not None else None,
        "explanation": _explain(len(placed_drawings), len(unplaced_drawings),
                                distance),
        "resolution": "本层已退回局部坐标系；补齐世界锚点后可改为绝对摆放",
    }


def _explain(placed: int, unplaced: int, distance: float | None) -> str:
    """把矛盾说成人能判断的话 —— 光给数字，人无从下手。"""
    head = (f"本层 {placed + unplaced} 张图里，只有 {placed} 张解出了世界锚点，"
            f"其余 {unplaced} 张只能按轴号相对配准。")
    if distance is not None:
        head += f"两组构件中心相距 **{distance:.0f} 米**，说明它们不在同一坐标系。"
    return head + (
        "处置有二：① 给其余图补世界锚点（在轴网识别面板确认分区号、"
        "或人工标定坐标标注），本层即可整体绝对摆放；"
        "② 若这些图本就没有坐标标注，保持局部配准即可 —— "
        "模型内部相对关系正确，只是不带工程坐标。"
    )


def summarize_conflicts(conflicts: list[dict[str, Any]]) -> dict[str, Any]:
    """全项目矛盾汇总，供 scene.quality 与前端展示。"""
    valid = [c for c in conflicts if c]
    return {
        "count": len(valid),
        "floors": [c["floor"] for c in valid],
        "items": valid,
    }
