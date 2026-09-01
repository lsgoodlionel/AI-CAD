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

@dataclass(frozen=True)
class MarkSpec:
    """一个构件代号的登记项。

    `source` 是**可回溯的出处**——此前这张表是凭记忆手写的，混进了
    非平法代号，也漏掉了 2022 版新增的代号。现在每条都记它从哪来。
    """
    code: str
    kind: str
    name: str
    source: str


#: 构件代号权威表。出处逐条登记，全部核对过图集原件
#: （`/Users/lionel/work/识图标准/平法图集/`，见 `core.knowledge.source_registry`）。
#:
#: **为什么旧版代号一并保留**：实测全库图纸说明引用 `16G101` 155 次、
#: `11G101` 48 次 —— 旧版图集仍在活跃使用，按 16G101 绘制的图上
#: 就是写 `KZZ`/`LZ`/`QZ`。删掉它们会让这批真实图纸失识。
MARK_SPECS: tuple[MarkSpec, ...] = (
    # ── 柱（22G101-1 表2.2.2-1，图集第 1-3 页，已核对原件）──
    MarkSpec("KZ", "column", "框架柱", "22G101-1 表2.2.2-1"),
    MarkSpec("ZHZ", "column", "转换柱", "22G101-1 表2.2.2-1"),
    MarkSpec("XZ", "column", "芯柱", "22G101-1 表2.2.2-1"),
    # 16G101-1 旧版柱代号：22 版把「框支柱 KZZ」改称「转换柱 ZHZ」，
    # 并取消 LZ/QZ（改为仍编 KZ）。按旧版绘制的图上仍在用。
    MarkSpec("KZZ", "column", "框支柱", "16G101-1（旧版，工程在用）"),
    MarkSpec("LZ", "column", "梁上柱", "16G101-1（旧版，工程在用）"),
    MarkSpec("QZ", "column", "剪力墙上柱", "16G101-1（旧版，工程在用）"),
    # 构造柱不属于 G101 平法体系，属砌体结构（22G614-1 / GB 50003）。
    MarkSpec("GZ", "column", "构造柱", "砌体结构（非平法）"),

    # ── 墙柱（22G101-1 表3.2.2-1，第 1-9 页，已核对原件）──
    MarkSpec("YBZ", "wall", "约束边缘构件", "22G101-1 表3.2.2-1"),
    MarkSpec("GBZ", "wall", "构造边缘构件", "22G101-1 表3.2.2-1"),
    MarkSpec("AZ", "wall", "非边缘暗柱", "22G101-1 表3.2.2-1"),
    MarkSpec("FBZ", "wall", "扶壁柱", "22G101-1 表3.2.2-1"),
    # 墙身 Q××（××排）
    MarkSpec("Q", "wall", "剪力墙身", "22G101-1 §3.2.2"),
    # 以下两个**在 22G101-1 与 16G101-1 的编号表中均查无出处**，
    # 是上一版本凭记忆写下的。保留以免破坏既有行为，但如实标注待核。
    MarkSpec("YZ", "wall", "翼柱（待核）", "unverified"),
    MarkSpec("JLQ", "wall", "剪力墙（待核）", "unverified"),

    # ── 墙梁（22G101-1 表3.2.2-2，第 1-10 页，已核对原件）──
    MarkSpec("LL", "beam", "连梁", "22G101-1 表3.2.2-2"),
    MarkSpec("LLk", "beam", "连梁（跨高比不小于 5）", "22G101-1 表3.2.2-2"),
    MarkSpec("LL(JC)", "beam", "连梁（对角暗撑配筋）", "22G101-1 表3.2.2-2"),
    MarkSpec("LL(JX)", "beam", "连梁（对角斜筋配筋）", "22G101-1 表3.2.2-2"),
    MarkSpec("LL(DX)", "beam", "连梁（集中对角斜筋配筋）", "22G101-1 表3.2.2-2"),
    MarkSpec("AL", "beam", "暗梁", "22G101-1 表3.2.2-2"),
    MarkSpec("BKL", "beam", "边框梁", "22G101-1 表3.2.2-2"),

    # ── 梁（22G101-1 表4.2.2，第 1-23 页，已核对原件）──
    MarkSpec("KL", "beam", "楼层框架梁", "22G101-1 表4.2.2"),
    MarkSpec("KBL", "beam", "楼层框架扁梁", "22G101-1 表4.2.2"),
    MarkSpec("KBH", "beam", "楼层框架扁梁节点核心区", "22G101-1 表4.2.2 注2"),
    MarkSpec("WKL", "beam", "屋面框架梁", "22G101-1 表4.2.2"),
    MarkSpec("KZL", "beam", "框支梁", "22G101-1 表4.2.2"),
    MarkSpec("TZL", "beam", "托柱转换梁", "22G101-1 表4.2.2"),
    MarkSpec("L", "beam", "非框架梁", "22G101-1 表4.2.2"),
    # 注3：端支座上部纵筋充分利用抗拉强度时代号后加 g（`Lg7(5)`）；
    # 注4：按受扭设计时代号后加 N（`LN5(3)`）。
    MarkSpec("Lg", "beam", "非框架梁（端支座充分利用抗拉）", "22G101-1 表4.2.2 注3"),
    MarkSpec("LN", "beam", "受扭非框架梁", "22G101-1 表4.2.2 注4"),
    MarkSpec("XL", "beam", "悬挑梁", "22G101-1 表4.2.2"),
    MarkSpec("JZL", "beam", "井字梁", "22G101-1 表4.2.2"),
    MarkSpec("JZLg", "beam", "井字梁（端支座充分利用抗拉）", "22G101-1 表4.2.2 注3"),

    # ── 板（22G101-1 表5.2.1，第 1-34 页，已核对原件）──
    MarkSpec("LB", "slab", "楼面板", "22G101-1 表5.2.1"),
    MarkSpec("WB", "slab", "屋面板", "22G101-1 表5.2.1"),
    MarkSpec("XB", "slab", "悬挑板", "22G101-1 表5.2.1"),
    MarkSpec("YXB", "slab", "（待核）", "unverified"),

    # ── 板式楼梯（22G101-2，梯板类型代号）──
    # **金标准实测：「墙」的最大误检源正是楼梯（31%）**，而此前这张表里
    # 一个楼梯代号都没有 —— `AT1` 只能落进 other，几何上又像并排的墙线。
    MarkSpec("AT", "stair", "AT 型梯板", "22G101-2"),
    MarkSpec("BT", "stair", "BT 型梯板", "22G101-2"),
    MarkSpec("CT", "stair", "CT 型梯板", "22G101-2"),
    MarkSpec("DT", "stair", "DT 型梯板", "22G101-2"),
    MarkSpec("ET", "stair", "ET 型梯板", "22G101-2"),
    MarkSpec("FT", "stair", "FT 型梯板", "22G101-2"),
    MarkSpec("GT", "stair", "GT 型梯板", "22G101-2"),
    MarkSpec("ATa", "stair", "ATa 型梯板（滑动支座）", "22G101-2"),
    MarkSpec("ATb", "stair", "ATb 型梯板（滑动支座）", "22G101-2"),
    MarkSpec("ATc", "stair", "ATc 型梯板（抗震）", "22G101-2"),
    MarkSpec("BTb", "stair", "BTb 型梯板（滑动支座）", "22G101-2 §2.2.7"),
    MarkSpec("CTa", "stair", "CTa 型梯板（滑动支座）", "22G101-2 §2.2.8"),
    MarkSpec("CTb", "stair", "CTb 型梯板（滑动支座）", "22G101-2 §2.2.8"),
    MarkSpec("DTb", "stair", "DTb 型梯板（滑动支座）", "22G101-2 §2.2.9"),

    # ── 基础（22G101-3，已核对表2.2 原件）──
    # **注意 j/z 而非 j/p**：独立基础是「阶形 j / 锥形 z」，
    # 条形基础底板才是「阶形 j / 坡形 p」。两者不同，极易记混。
    MarkSpec("DJj", "foundation", "阶形普通独立基础", "22G101-3 表2.2"),
    MarkSpec("DJz", "foundation", "锥形普通独立基础", "22G101-3 表2.2"),
    MarkSpec("BJj", "foundation", "阶形杯口独立基础", "22G101-3 表2.2"),
    MarkSpec("BJz", "foundation", "锥形杯口独立基础", "22G101-3 表2.2"),
    MarkSpec("TJBp", "foundation", "坡形条形基础底板", "22G101-3"),
    MarkSpec("TJBj", "foundation", "阶形条形基础底板", "22G101-3"),
    MarkSpec("JL", "foundation", "基础梁 / 基础主梁", "22G101-3"),
    MarkSpec("JCL", "foundation", "基础次梁", "22G101-3"),
    MarkSpec("LPB", "foundation", "梁板式筏基平板", "22G101-3"),
    MarkSpec("ZXB", "foundation", "柱下板带", "22G101-3"),
    MarkSpec("KZB", "foundation", "跨中板带", "22G101-3"),
    MarkSpec("BPB", "foundation", "平板式筏基平板", "22G101-3"),
    MarkSpec("CTj", "foundation", "阶形承台", "22G101-3"),
    MarkSpec("CTz", "foundation", "锥形承台", "22G101-3"),
    MarkSpec("CTL", "foundation", "承台梁", "22G101-3"),
    MarkSpec("GZH", "foundation", "灌注桩", "22G101-3"),

    # ── 门窗（GB/T 50104 建筑制图习惯代号，非 G101）──
    MarkSpec("FM", "door", "防火门", "建筑制图习惯"),
    MarkSpec("MC", "door", "门联窗", "建筑制图习惯"),
    MarkSpec("LC", "window", "铝合金窗", "建筑制图习惯"),
    MarkSpec("M", "door", "门", "建筑制图习惯"),
    MarkSpec("C", "window", "窗", "建筑制图习惯"),
)

#: 代号 → 类别（保持既有内部形态）。
_MARK_KINDS: tuple[tuple[str, str], ...] = tuple(
    (spec.code, spec.kind) for spec in MARK_SPECS)

_SPEC_BY_CODE: dict[str, MarkSpec] = {spec.code: spec for spec in MARK_SPECS}


def mark_spec(code: str) -> MarkSpec | None:
    """代号 → 登记项（含中文名与出处）。"""
    return _SPEC_BY_CODE.get(code)


#: 结构族类别 —— 这些代号**只在结构/建筑图上成立**。
STRUCTURAL_KINDS = frozenset(
    {"column", "beam", "wall", "slab", "stair", "foundation"})

#: 机电专业标识（`drawings.discipline` 的取值）。
_MEP_DISCIPLINES = frozenset({"mep", "electrical", "hvac", "plumbing", "机电", "电气"})


def _is_mep(discipline: str | None) -> bool:
    return str(discipline or "").strip().lower() in _MEP_DISCIPLINES


#: 编号主体：代号 + 序号（+ 可选跨数后缀 `(3)` / `(3A)` / `(2B)`）。
#: 跨数后缀是平法特有写法：`KL1(3)` = 1 号框架梁 3 跨，`A`/`B` 表一端/两端悬挑。
#:
#: **代号改为按已知表穷举匹配**，不再用 `[A-Z]{1,3}` 泛匹配。两个原因：
#: ① 平法代号**合法地含小写**（`LLk`、`DJj`、`ATa`、`Lg`），泛匹配写死了
#:    大写字母类，这批代号一个也认不出；
#: ② 泛匹配先切出形似代号再查表，长短代号的先后顺序成了隐性依赖。
#: 穷举按长度降序排列，`ATa1` 不会被 `AT` 先吃掉。
_MARK_RE = re.compile(
    r"^(?P<code>"
    + "|".join(re.escape(spec.code) for spec in
               sorted(MARK_SPECS, key=lambda spec: -len(spec.code)))
    + r")(?P<seq>\d{1,4})(?P<span>\((?:\d{1,2})[AB]?\))?$")


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


def parse_component_mark(text: str | None, *,
                         discipline: str | None = None) -> ComponentMark | None:
    """解析平法构件编号；不是编号返回 None（**判不出就不猜**）。

    `discipline` 是这张图的专业（`drawings.discipline`）。给了它就能挡掉
    **形态相同、语义不同**的机电编号 —— 这是实测逼出来的：

    - `LN1`~`LN14` 在全库出现 **1144 次，全部在 mep 的配电系统图上**，
      是照明回路编号，不是 22G101 的「受扭非框架梁」；
    - `CT2` 出现在电气的「基础接地平面」上，是电流互感器，不是 CT 型梯板。

    反过来，`AT1`/`BT4`/`DT6` 实测**全部落在 structure 专业的
    「楼梯 ST-xx 结构详图」**上 —— 那是真的梯板编号。所以判据不是
    「这些代号可疑」，而是**专业不对就不认**。

    不传 `discipline` 时不做这层过滤（保持向后兼容），
    但只要调用方拿得到专业，就应该传。
    """
    raw = str(text or "").strip()
    # **不能再要求整串大写**：`LLk`/`DJj`/`ATa`/`Lg` 是国标规定的写法，
    # 整串大写的旧判据会把这些代号**全部拒之门外**。
    # 代号本身已由 `_MARK_RE` 按权威表穷举匹配（大小写敏感），
    # 所以 `kz1`、`q35` 这类小写串自然匹配不上，无需额外守卫。
    if not raw:
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
        if kind in STRUCTURAL_KINDS and _is_mep(discipline):
            return None                  # 机电图上的同形编号不是结构构件
        return ComponentMark(kind=kind, code=code, seq=seq,
                             span=matched.group("span"), raw=raw)
    return None
