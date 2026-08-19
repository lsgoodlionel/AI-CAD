"""构造做法层识别（「厚度 + 材料」）。

**为什么做**：会审 133 条检查项里「做法」出现 **47 条**，
是仅次于「说明」的第二高频要素 —— 它通往**真实构造层次**：
一块「100 厚」的板，实际是「20 厚砂浆找平 + 3 厚防水 +
100 厚结构板 + 30 厚保温」。建模用的厚度、算量用的材料量都在这里。

**两个数据库实测形态**：

    20厚DSM20预拌砂浆找平层        39 次
    3.0厚自粘改性沥青防水卷材      24 次
    30厚带铝箔岩棉板内保温         54 次
    ALC预制板斜墙100厚             66 次   ← 厚度在**后**

**必须防的误判**：带编号的设计说明同样含「厚」——
`9.未注明楼梯平台板：板厚h=120，配筋…`、`8.…（梁高一板厚）≥450…`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 构造层厚度的合理区间（mm）。防水涂膜薄至 1.5，垫层厚至数百；
#: 超出即不是构造层（`5000厚` 更可能是别的数）。
MIN_THICKNESS_MM = 1.0
MAX_THICKNESS_MM = 800.0

#: 「厚度 + 材料」：`20厚…` / `3.0厚…`
_THICK_FIRST_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*厚\s*(.+)$")
#: 「材料 + 厚度」：`ALC预制板斜墙100厚`（实测 66 次）
_THICK_LAST_RE = re.compile(r"^(.+?)(\d+(?:\.\d+)?)\s*厚\s*$")

#: **设计说明的指纹**：带条目编号、含冒号/等号的整句。
#: 实测 `9.未注明楼梯平台板：板厚h=120，配筋双层双向钢筋10@200。`
#: `(?!\d)` 不可省：`1.5厚…` 的 `1.` 会被当成条目编号 ——
#: 条目编号后跟的是文字（`9.未注明…`），小数点后跟的是数字。
_DESIGN_NOTE_RE = re.compile(
    r"^\s*\d+\s*[.、](?!\d)|[：:＝=]|未注明|另有说明|见下表|应设")

#: 构造层的**作用**（建模时保温层与结构层处理完全不同）。
#: 顺序即优先级 —— `钢筋混凝土板` 同时含「混凝土」与「板」，结构层优先。
_ROLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # **结构层必须分墙与板**：实测 `structural` 里混着
    # `100厚ALC预制板斜墙`（墙）与 `120厚现浇钢筋混凝土板`（板），
    # 拿它做板厚会**把墙厚当板厚**。两者由 `_structural_role` 按
    # **末位构件词**判定（谁在最后，说的就是谁）。
    ("structural", ("钢筋混凝土板", "现浇板", "结构板", "楼板", "屋面板",
                    "预制板", "叠合板", "砌块", "砌体", "墙体", "隔墙",
                    "轻质墙", "板墙", "墙板", "斜墙")),
    ("waterproof", ("防水", "隔汽", "防潮")),
    ("insulation", ("保温", "岩棉", "挤塑", "聚苯", "隔热", "隔声", "吸声")),
    ("cushion", ("垫层", "素土", "夯实", "碎石", "级配")),
    ("leveling", ("找平", "找坡", "抹面", "砂浆", "结合层")),
    ("finish", ("面层", "面砖", "地砖", "石材", "涂料", "饰面", "涂层")),
)


@dataclass(frozen=True)
class ConstructionLayer:
    """一层构造做法。"""
    thickness_mm: float
    material: str
    role: str          # structural_slab / structural_wall / waterproof /
                       # insulation / cushion / leveling / finish / other
    raw: str


#: 结构层里区分墙与板的构件词。**以末位者为准** ——
#: `ALC预制板斜墙` 同时含「板」与「墙」，它是用板材砌的**墙**；
#: `墙上现浇板` 反过来是**板**。
_WALL_WORDS = ("墙", "砌块", "砌体")
_SLAB_WORDS = ("板",)


#: **复合词优先于末位规则**。「墙板」是做墙用的板材，整体指墙，
#: 但「板」在末位 —— 中文复合词拆开看会判反（实测 `100厚轻质墙板`）。
_WALL_COMPOUNDS = ("墙板", "隔墙板", "条板墙", "墙体板", "板式墙")


def _structural_role(material: str) -> str:
    """结构层是墙还是板。

    先查**复合词**（`墙板` 整体指墙），再取**最后出现**的构件词
    （`ALC预制板斜墙` 是墙、`墙上现浇板` 是板）。
    """
    if any(word in material for word in _WALL_COMPOUNDS):
        return "structural_wall"
    last_wall = max((material.rfind(w) for w in _WALL_WORDS), default=-1)
    last_slab = max((material.rfind(w) for w in _SLAB_WORDS), default=-1)
    return "structural_wall" if last_wall > last_slab else "structural_slab"


def _classify_role(material: str) -> str:
    for role, keywords in _ROLE_RULES:
        if any(word in material for word in keywords):
            return _structural_role(material) if role == "structural" else role
    return "other"


def parse_construction_layer(text: str | None) -> ConstructionLayer | None:
    """解析一层构造做法；不是构造层返回 None（**判不出就不猜**）。"""
    body = str(text or "").strip()
    if not body or "厚" not in body:
        return None
    if _DESIGN_NOTE_RE.search(body):
        return None                    # 设计说明，不是构造层

    matched = _THICK_FIRST_RE.match(body)
    if matched:
        thickness, material = matched.group(1), matched.group(2).strip()
    else:
        matched = _THICK_LAST_RE.match(body)
        if not matched:
            return None
        material, thickness = matched.group(1).strip(), matched.group(2)
    if not material:
        return None
    try:
        value = float(thickness)
    except ValueError:
        return None
    if not MIN_THICKNESS_MM <= value <= MAX_THICKNESS_MM:
        return None
    return ConstructionLayer(thickness_mm=value, material=material,
                             role=_classify_role(material), raw=body)
