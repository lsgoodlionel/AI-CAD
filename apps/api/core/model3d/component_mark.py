"""平法构件编号识别（22G101 系列国标图集）。

**为什么做这个**：19 个专业 133 条会审检查项里，「编号」出现 **38 条**，
是仅次于说明/做法/详图的第四高频要素 —— 人核图的主要抓手正是它：
看到平面图标 `KZ1`，就去柱表查它的配筋与截面。

两个工程的档案层里现存约 **2.5 万条**这类编号，此前全落在 `other`/`note`。

编号的价值有三层：
1. **直接标明构件类型** —— `KZ` 就是框架柱，比几何猜测（填充多边形+尺寸）可靠
2. **跨图关联的主键** —— 平面图的 `KZ1` ↔ 柱表的 `KZ1`
3. **通往真实截面** —— 构件表里有截面尺寸与配筋，不必再靠比例尺估算

依据 22G101-1《混凝土结构施工图平面整体表示方法制图规则和构造详图
（现浇混凝土框架、剪力墙、梁、板）》的构件代号表。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 22G101 构件代号 → 构件类别。**长代号必须排在短代号前**，
#: 否则 `KZZ3` 会先被 `KZ` 吃掉一半（正则按顺序择一匹配）。
_MARK_KINDS: tuple[tuple[str, str], ...] = (
    # 柱：框架柱/框架核心柱/芯柱/梁上柱/剪力墙上柱/构造柱
    ("KZZ", "column"), ("KZ", "column"), ("XZ", "column"),
    ("LZ", "column"), ("QZ", "column"), ("GZ", "column"),
    # 墙：约束/构造边缘构件、暗柱、翼柱、扶壁柱、剪力墙身
    ("YBZ", "wall"), ("GBZ", "wall"), ("AZ", "wall"), ("YZ", "wall"),
    ("FBZ", "wall"), ("JLQ", "wall"), ("Q", "wall"),
    # 梁：屋面框架梁/框支梁/框架梁/井字梁/悬挑梁/连梁/非框架梁
    ("WKL", "beam"), ("KZL", "beam"), ("KL", "beam"), ("JZL", "beam"),
    ("XL", "beam"), ("LL", "beam"), ("L", "beam"),
    # 板：延性板带/悬挑板/屋面板/楼面板
    ("YXB", "slab"), ("WB", "slab"), ("LB", "slab"), ("XB", "slab"),
    # 门窗：防火门/门联窗/铝合金窗/门/窗
    ("FM", "door"), ("MC", "door"), ("LC", "window"),
    ("M", "door"), ("C", "window"),
)

#: 编号主体：代号 + 序号（+ 可选跨数后缀 `(3)` / `(3A)` / `(2B)`）。
#: 跨数后缀是平法特有写法：`KL1(3)` = 1 号框架梁 3 跨，`A`/`B` 表一端/两端悬挑。
_MARK_RE = re.compile(
    r"^(?P<code>[A-Z]{1,3})(?P<seq>\d{1,4})(?P<span>\((?:\d{1,2})[AB]?\))?$")

#: **必须排除的形近串**（实测：`C65H-C16A/2P+V` 出现 488 次，是断路器型号）。
#: 复合符号是设备型号/钢筋规格的指纹，构件编号里不会有。
_NOT_A_MARK_RE = re.compile(r"[/@+×*%·]|--|[a-z]{2,}")

#: 材料强度等级：`C30` 混凝土、`M10` 砂浆 —— **形态与门窗编号完全相同**，
#: 只能靠取值范围分开。GB 50010：混凝土 C15~C80（5 的倍数）；
#: GB/T 25181：砂浆 M2.5~M30。
_CONCRETE_GRADES = {15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80}
_MORTAR_GRADES = {2, 5, 10, 15, 20, 25, 30}
#: 钢材牌号（GB/T 700 碳素结构钢 / GB/T 1591 低合金高强度结构钢）——
#: `Q235` 与剪力墙编号 `Q1` 形态相同，实测在第二工程被误判为墙。
_STEEL_GRADES = {195, 215, 235, 275, 345, 355, 390, 420, 460, 500, 550, 620, 690}


@dataclass(frozen=True)
class ComponentMark:
    """一个平法构件编号。"""
    kind: str          # column / beam / wall / slab / door / window
    code: str          # 构件代号，如 KZ
    seq: int           # 序号
    span: str | None   # 跨数后缀，如 (3A)
    raw: str


def _is_material_grade(code: str, seq: int) -> bool:
    """`C30` 是混凝土强度、`M10` 是砂浆强度，不是构件编号。"""
    if code == "C":
        return seq in _CONCRETE_GRADES
    if code == "M":
        return seq in _MORTAR_GRADES
    if code == "Q":
        return seq in _STEEL_GRADES
    return False


#: 结构构件序号的硬上限。实测误判：`L1200` 是尺寸（长 1200mm）而非
#: 1200 号梁、`LB1441` 序号过大。三位以内覆盖所有真实工程。
MAX_STRUCTURAL_SEQ = 999

#: 门窗**序号式**编号的上限。一个工程的门窗**种类**可能上百，
#: 但不会近千 —— 实测轨道交通的 `C769` 落在这个区间外，
#: 它更可能是尺寸或别的编码。**这是经验阈值，不是国标规定**：
#: 定高会放进噪声，定低会漏掉超大项目的真实编号，取 199 兼顾两头。
MAX_OPENING_SEQ = 199

#: 门窗**宽高编码**的合理区间（分米）：`M1124` = 1100×2400。
#: 宽高各两位、且都 ≥ 04（0.4 米）—— 比这更小的洞口不会单独编号。
MIN_OPENING_DECIMETRE = 4


def _is_valid_opening_size_code(seq_text: str) -> bool:
    """四位门窗编号是否为合理的宽高编码。"""
    if len(seq_text) != 4:
        return False
    width, height = int(seq_text[:2]), int(seq_text[2:])
    return width >= MIN_OPENING_DECIMETRE and height >= MIN_OPENING_DECIMETRE


def parse_component_mark(text: str | None) -> ComponentMark | None:
    """解析平法构件编号；不是编号返回 None（**判不出就不猜**）。"""
    raw = str(text or "").strip()
    # **平法编号在图上是大写** —— 小写多是图层名/代号（实测 `q35`），
    # 不该靠 `.upper()` 强行认下。
    if not raw or raw != raw.upper():
        return None
    body = raw
    if _NOT_A_MARK_RE.search(raw):
        return None
    matched = _MARK_RE.match(body)
    if not matched:
        return None
    code_full = matched.group("code")
    seq = int(matched.group("seq"))
    for code, kind in _MARK_KINDS:
        if code_full != code:
            continue
        if _is_material_grade(code, seq):
            return None
        if seq < 1:                      # 编号从 1 起，没有 0 号（实测 `M0`/`Q0`）
            return None
        seq_text = matched.group("seq")
        if kind in ("door", "window"):
            # 门窗：序号式（1~999）或宽高编码式（四位，如 M1124）
            if len(seq_text) == 4 and not _is_valid_opening_size_code(seq_text):
                return None
            if len(seq_text) <= 3 and seq > MAX_OPENING_SEQ:
                return None
        elif seq > MAX_STRUCTURAL_SEQ or len(seq_text) > 3:
            return None                  # 结构构件序号上限（实测 `L1200` 是尺寸）
        return ComponentMark(kind=kind, code=code, seq=seq,
                             span=matched.group("span"), raw=raw)
    return None
