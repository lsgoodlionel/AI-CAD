"""轴网位姿求解：把共识从「仅平移」扩展到**完整相似变换**。

**为什么必须做**（v80 实测）：逐方向共识把 12 层的轴网都算了出来，
落库却只有 5 层 —— 剔除的 7 层（FD/B3/B1/F3/F5/F6/RF_HIGH）
根因一致：**比例或旋转错误，平移不可修**。

**推导**：x/y 轴号各自只有一维位置，看似解不了旋转；
但两者的**笛卡尔积**给出二维锚点 —— 轴号 `1` 与 `A` 的交点
就是图上一个确定的点。同名交点在两图间配对即可解相似变换，
复用既有的 `similarity_from_pairs`（它已支持反射：
工程坐标 X=北/Y=东 是左手系，不支持时实测残差 105 米）。

这是完整的二维 Bundle Adjustment，共识的平移解是它的退化情形。

**边界**：本模块只求解，不判定该不该用 —— 采纳与否由调用方按
`rmse` 与 `scale` 的合理性决定（比例应接近 1，偏离说明上游
`drawing_transform` 的比例尺本身就错了，那要回上游修）。
"""
from __future__ import annotations

import re

from services.drawing_anchor import MIN_PAIRS, similarity_from_pairs

#: 交点数上限：轴网 x×y 可能上千，解相似变换用不了那么多，
#: 且全量配对会让 O(n²) 的调用方吃不消。取位置有序的前 N×N。
MAX_AXES_PER_DIRECTION = 40


#: 分区前缀（§8.0.5「分区号-轴线号」）。**不含 `/`** —— 那是附加轴线的
#: 分数式（§8.0.6 `2-1/k`），整体是一个标签，拆开会毁掉它的身份。
_ZONE_PREFIX_RE = re.compile(r"^\d+-(?![^/]*/)(.+)$")


def normalize_axis_label(label: str | None) -> str:
    """轴号 → **跨体系配对键**（剥掉分区前缀）。

    **实测根因**：被判外点的图轴号是 `1-1,1-2,1-3`（带分区前缀），
    全局是 `1,2,3`（裸轴号），共有标签 0 个 —— 不是几何错，
    是同一栋楼的轴网被两套命名体系割裂。
    全项目实测：裸标签 **691** 张、分区标签 **77** 张、同图混用 **108** 张。

    §8.0.5/§8.0.6：分区号是前缀，`1-1` 的轴线序号就是 `1`。

    **只用于配对，不改变原标签** —— 不同分区的 `1-1` 与 `2-1`
    归一后都是 `1`，若用于合并会撞身份（这正是本轮修过的锚点串图）。
    调用方必须保证候选同属一个单体/分区。
    """
    text = str(label or "").strip()
    matched = _ZONE_PREFIX_RE.match(text)
    return matched.group(1) if matched else text


def _entries(axes: dict | None, direction: str) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for label, position in (axes or {}).get(direction) or ():
        text = str(label or "").strip()
        if not text:
            continue
        try:
            out.append((text, float(position)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda e: e[1])
    return out[:MAX_AXES_PER_DIRECTION]


def intersections_of(axes: dict | None) -> dict[tuple[str, str], tuple[float, float]]:
    """轴网 → **交点字典** `{(x轴号, y轴号): (px, py)}`。

    x/y 轴号的笛卡尔积就是图上的定位交点 —— 这是把一维轴线位置
    变成二维锚点的关键一步。单方向轴网构不出交点，返回空。
    """
    xs = _entries(axes, "x")
    ys = _entries(axes, "y")
    if not xs or not ys:
        return {}
    # 键用**归一化标签**，让分区体系（`1-1`）与裸体系（`1`）能配对；
    # 值仍是原始位置，归一化不改变几何。
    return {
        (normalize_axis_label(lx), normalize_axis_label(ly)): (px, py)
        for lx, px in xs
        for ly, py in ys
    }


def solve_pose_from_points(
    local_points: dict[tuple[str, str], tuple[float, float]] | None,
    global_points: dict[tuple[str, str], tuple[float, float]] | None,
) -> dict | None:
    """由**同名交点**解相似变换 local → global。

    共有交点不足 `MIN_PAIRS` 时返回 None（**证据不足不猜**）。
    """
    local_points = local_points or {}
    global_points = global_points or {}
    shared = sorted(set(local_points) & set(global_points))
    if len(shared) < MIN_PAIRS:
        return None
    src = [local_points[key] for key in shared]
    dst = [global_points[key] for key in shared]
    pose = similarity_from_pairs(src, dst)
    if pose is None:
        return None
    return {**pose, "shared": len(shared)}


def solve_pose_to_global(local_axes: dict | None,
                         global_axes: dict | None) -> dict | None:
    """该图轴网 → 全局轴网的相似变换（缩放/旋转/平移 + rmse）。"""
    return solve_pose_from_points(intersections_of(local_axes),
                                  intersections_of(global_axes))
