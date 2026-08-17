"""人工标定轴线基准 —— 绕开 OCR 轴号瓶颈,作为大范围识别的参考系。纯函数 + 仓储。

## 为什么需要

自动轴号识别撞到物理上限:档案 OCR 文本筛出的「轴号」位置序与数值序仅 **0.3%** 一致
(中位逆序率 0.60);改用轴号圈检测 + 圈内 OCR 后逆序率降至 **0.21**,仍未过可用门槛
0.15,瓶颈是通用 OCR 在小图块单字符上的能力(见蓝图 §9.12/§9.13)。

**人工标定少量基准**即可绕开:人在图上点两点定一条轴线并写轴号,系统据此
① 得到该图**可信轴网**;② 若给出相邻轴线实际轴距,**直接反算比例尺**(比读文字更可靠);
③ 以该图为**参考帧**,让其他图按同名轴线对齐(配准的锚)。

标定时机不限:上传图纸时 / 建模过程中 / 建模完毕的修正阶段。
"""
from __future__ import annotations

from typing import Any

#: 轴线近似垂直/水平的容差(归一化坐标):超出说明标歪了
STRAIGHTNESS_TOLERANCE = 0.05


def axis_position(ref: dict) -> float | None:
    """人工轴线 → 其在垂直于自身方向上的位置(归一化)。

    direction='x'(竖向轴线,数字号):位置取 x 均值;'y'(横向):取 y 均值。
    线段不够直(超容差)→ None(标歪了,不可用作基准)。

    **'skew'(斜向轴线)另算**:斜向轴线是合法轴线(放射柱网/异形平面常见),
    位置用法线式偏移表示,见 `axis_geometry.axis_offset`。
    """
    direction = str(ref.get("direction") or "")
    x1, y1 = float(ref.get("x1_norm", 0)), float(ref.get("y1_norm", 0))
    x2, y2 = float(ref.get("x2_norm", 0)), float(ref.get("y2_norm", 0))
    if direction == "skew":
        from services.axis_geometry import axis_offset
        return axis_offset(ref)
    if direction == "x":
        if abs(x1 - x2) > STRAIGHTNESS_TOLERANCE:
            return None
        return (x1 + x2) / 2
    if direction == "y":
        if abs(y1 - y2) > STRAIGHTNESS_TOLERANCE:
            return None
        return (y1 + y2) / 2
    return None


def scale_from_spacing(refs: list[dict], page_h_pt: float) -> dict | None:
    """由「相邻轴线实际轴距」反算比例尺 → {scale_m_pt, samples, spread}。

    人标注两条同向轴线并填写实际轴距(如 1 轴到 2 轴 8400mm)后:
        scale(m/pt) = 轴距(m) / 图上距离(pt)
    图上距离 = |位置差(归一化)| × page_h。多组样本取中位数,并给出离散度供判可信。
    """
    if not page_h_pt:
        return None
    samples: list[float] = []
    by_dir: dict[str, list[dict]] = {}
    for ref in refs or []:
        pos = axis_position(ref)
        if pos is not None:
            by_dir.setdefault(str(ref.get("direction")), []).append({**ref, "_pos": pos})
    for items in by_dir.values():
        items.sort(key=lambda r: r["_pos"])
        for prev, cur in zip(items, items[1:]):
            spacing_mm = cur.get("spacing_to_prev_mm")
            if not spacing_mm:
                continue
            delta_pt = abs(cur["_pos"] - prev["_pos"]) * page_h_pt
            if delta_pt <= 1e-6:
                continue
            samples.append((float(spacing_mm) / 1000.0) / delta_pt)
    if not samples:
        return None
    samples.sort()
    median = samples[len(samples) // 2]
    spread = (samples[-1] - samples[0]) / median if median else 0.0
    return {"scale_m_pt": round(median, 8), "samples": len(samples),
            "spread": round(spread, 4)}


def to_scene_axes(refs: list[dict], transform: Any) -> dict:
    """人工轴线 → scene 轴网格式 {"x":[{label,coord}], "y":[...]}(米坐标)。

    归一化位置 → 页面 pt → 经该图 transform 转米,与构件同坐标系。
    """
    from services.drawing_transform import pt_to_meter

    page_h = float(getattr(transform, "page_h", 0) or 0)
    out: dict[str, list] = {"x": [], "y": []}
    if not page_h:
        return out
    for ref in refs or []:
        pos = axis_position(ref)
        if pos is None:
            continue
        direction = str(ref.get("direction"))
        pt = pos * page_h
        # 竖向轴线:x 位置取决于 pt;横向轴线:y 位置
        x_m, y_m = pt_to_meter(pt if direction == "x" else 0.0,
                               pt if direction == "y" else 0.0, transform)
        coord = x_m if direction == "x" else y_m
        out.setdefault(direction, []).append(
            {"label": str(ref.get("label") or "").strip(), "coord": round(coord, 3)})
    out["x"].sort(key=lambda e: e["coord"])
    out["y"].sort(key=lambda e: e["coord"])
    return out


# ── 仓储 ─────────────────────────────────────────────────────────

_UPSERT_SQL = """
INSERT INTO manual_axis_references
    (project_id, drawing_id, label, direction, x1_norm, y1_norm, x2_norm, y2_norm,
     spacing_to_prev_mm, note, created_by)
VALUES (:project_id, :drawing_id, :label, :direction, :x1, :y1, :x2, :y2,
        :spacing, :note, :created_by)
ON CONFLICT (drawing_id, direction, label) DO UPDATE SET
    x1_norm = EXCLUDED.x1_norm, y1_norm = EXCLUDED.y1_norm,
    x2_norm = EXCLUDED.x2_norm, y2_norm = EXCLUDED.y2_norm,
    spacing_to_prev_mm = EXCLUDED.spacing_to_prev_mm,
    note = EXCLUDED.note, updated_at = now()
RETURNING id
"""

_FETCH_SQL = """
SELECT id, drawing_id, label, direction, x1_norm, y1_norm, x2_norm, y2_norm,
       spacing_to_prev_mm, note
FROM manual_axis_references WHERE drawing_id = :drawing_id
ORDER BY direction, label
"""

_FETCH_PROJECT_SQL = """
SELECT drawing_id, label, direction, x1_norm, y1_norm, x2_norm, y2_norm,
       spacing_to_prev_mm
FROM manual_axis_references WHERE project_id = :project_id
"""


async def save_axis(db: Any, project_id: str, drawing_id: str, ref: dict,
                    created_by: str | None = None) -> str | None:
    """保存/更新一条人工轴线(同图同向同轴号幂等覆盖)。"""
    row = await db.fetch_one(_UPSERT_SQL, {
        "project_id": project_id, "drawing_id": drawing_id,
        "label": str(ref["label"]).strip(), "direction": str(ref["direction"]),
        "x1": float(ref["x1_norm"]), "y1": float(ref["y1_norm"]),
        "x2": float(ref["x2_norm"]), "y2": float(ref["y2_norm"]),
        "spacing": ref.get("spacing_to_prev_mm"), "note": ref.get("note"),
        "created_by": created_by,
    })
    return str(row["id"]) if row is not None else None


async def fetch_drawing_axes(db: Any, drawing_id: str) -> list[dict]:
    return [dict(r) for r in await db.fetch_all(_FETCH_SQL, {"drawing_id": drawing_id})]


async def fetch_project_axes(db: Any, project_id: str) -> dict[str, list[dict]]:
    """全项目人工轴线,按 drawing_id 分组。"""
    out: dict[str, list[dict]] = {}
    for r in await db.fetch_all(_FETCH_PROJECT_SQL, {"project_id": project_id}):
        d = dict(r)
        out.setdefault(str(d["drawing_id"]), []).append(d)
    return out


async def delete_axis(db: Any, drawing_id: str, direction: str, label: str) -> None:
    await db.execute(
        "DELETE FROM manual_axis_references "
        "WHERE drawing_id = :d AND direction = :dir AND label = :l",
        {"d": drawing_id, "dir": direction, "l": label})
