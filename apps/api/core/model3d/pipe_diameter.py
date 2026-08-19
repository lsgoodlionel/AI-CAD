"""管道公称直径识别（GB/T 1047《管道元件 DN(公称尺寸)的定义和选用》）。

**为什么做**：管径此前是**硬编码**的（CLAUDE.md「构件截面表替换硬编码
梁高/板厚/管径」），而图上白纸黑字标着 `DN100`。
实测机电图纸里 `DN100` 出现 178 次、`DN150` 163 次、`DN50` 155 次 ——
真实规格一直躺在档案层里没被用。

三种前缀（图上都见得到）：
- `DN` —— **公称尺寸**，管道元件的规格主键（不是实测内径或外径）
- `De` —— **外径**，塑料管（PPR/PE）常用
- `D`  —— 图上有时简写，按外径处理
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: GB/T 1047 常用公称尺寸系列（mm）。非标值**照收但标记**，
#: 图上真写了 `DN137` 就该留档，由人去判是不是笔误。
STANDARD_DN = (
    6, 8, 10, 15, 20, 25, 32, 40, 50, 65, 80, 100, 125, 150, 200,
    250, 300, 350, 400, 450, 500, 600, 700, 800, 900, 1000, 1200,
)

#: **塑料管外径有独立的标准系列**（GB/T 13663 给水用聚乙烯管材等）。
#: 实测 `De75` 出现 70 次，用 DN 的表去判会把整条 De 系列判成非标：
#:   DN 系列 …40 / 50 / **65** / **80** / 100…
#:   De 系列 …40 / 50 / **63** / **75** / **90** / **110**…
STANDARD_DE = (
    16, 20, 25, 32, 40, 50, 63, 75, 90, 110, 125, 140, 160, 180,
    200, 225, 250, 280, 315, 355, 400, 450, 500, 630,
)

#: 管径的合理区间（mm）—— 再小不成管、再大不是建筑给排水/暖通的量级。
MIN_DIAMETER_MM = 6.0
MAX_DIAMETER_MM = 4000.0

_DIAMETER_RE = re.compile(
    r"^(?P<prefix>DN|DE|D)\s*(?P<value>\d{1,4})(?:MM)?$", re.IGNORECASE)


@dataclass(frozen=True)
class PipeDiameter:
    """一个管径标注。"""
    dn_mm: float
    kind: str          # nominal（DN）/ outer（De、D）
    is_standard: bool  # 是否落在 GB/T 1047 常用系列
    raw: str


def parse_pipe_diameter(text: str | None) -> PipeDiameter | None:
    """解析管径标注；不是管径返回 None（**判不出就不猜**）。"""
    body = str(text or "").strip()
    if not body:
        return None
    matched = _DIAMETER_RE.match(body.replace(" ", ""))
    if not matched:
        return None
    try:
        value = float(matched.group("value"))
    except ValueError:
        return None
    if not MIN_DIAMETER_MM <= value <= MAX_DIAMETER_MM:
        return None
    prefix = matched.group("prefix").upper()
    kind = "nominal" if prefix == "DN" else "outer"
    series = STANDARD_DN if kind == "nominal" else STANDARD_DE
    return PipeDiameter(
        dn_mm=value,
        kind=kind,
        is_standard=int(value) in series,
        raw=body,
    )
