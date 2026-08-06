"""识图规则内置:GB/T 50001 条款的**单一来源**与可执行校验。

**为什么要有它**:国标条款此前散落在各识别器的 docstring 里,同一条规则
(如「I、O、Z 不得用作轴线编号」)在两处各写一遍,改一处就会漂移。
这里把常量收成一份,并给每条规则登记**条款号、生效模块、实测依据**。

**它必须是可执行的**,不是文档堆砌——核心是 `validate_axis_labels`:
按 §8.0.3/§8.0.4/§8.0.5/§8.0.6 校验推导出的轴号序列,把违规如实列出来。
推导链本身不保证正确(实测踩过:附加轴线混进主序列导致其后轴号整体偏移),
有了独立校验才能在**出结果时就发现**,而不是等人工看模型时才发现。

依赖方向:本模块不 import 任何识别器,识别器反过来 import 它。
"""
from __future__ import annotations

import re

#: §8.0.4 不得用作轴线编号的字母(与 1、0、2 易混)
FORBIDDEN_AXIS_LETTERS = frozenset({"I", "O", "Z"})

#: §8.0.3/§8.0.4 字母轴号可用序列(A 起、跳过 I·O·Z)
AXIS_LETTERS = "".join(
    ch for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if ch not in FORBIDDEN_AXIS_LETTERS
)

#: §8.0.2 轴号圆直径(mm)
LABEL_CIRCLE_DIAMETER_MM = (8.0, 10.0)

#: §4.0.2 线型表 → 制图语义
LINE_TYPE_PURPOSE = {
    "dash_dot": "轴线",                      # 单点长画线:中心线/对称线/轴线
    "dash_dot_dot": "外轮廓/用地界线",        # 双点长画线:假想轮廓线
    "dashed": "不可见轮廓",
    "solid": "构件轮廓/图框",
}

#: 条款登记表。每条记:原文要点 / 在哪生效 / 实测依据。
#: **实测依据是它区别于「抄一遍标准」的地方**——没有依据的条款也要如实留空。
CLAUSES: dict[str, dict] = {
    "4.0.2": {
        "text": "线型表:单点长画线(细)用于中心线、对称线、轴线;"
                "双点长画线(细)用于假想轮廓线、成型前原始轮廓线。",
        "applied_in": "core.model3d.line_type_classifier",
        "evidence": "A-01-02A 轴线实测长划 10.5pt + 短划 2.1pt + 空 2.1pt,严格周期。",
    },
    "4.0.6": {
        "text": "虚线、单点长画线、双点长画线的线段长度和间隔宜各自相等。",
        "applied_in": "core.model3d.line_type_classifier(节奏聚类)",
        "evidence": "同类划长实测 10.5~10.6,差异 <1%。",
    },
    "4.0.8": {
        "text": "点划线两端不应采用点(须以长画收尾);与其他图线交接时应线段交接。",
        "applied_in": "(待落地,可作节奏校验)",
        "evidence": "",
    },
    "8.0.1": {
        "text": "定位轴线应用 0.25b 线宽的单点长画线绘制。",
        "applied_in": "core.model3d.vector_axis_extractor(线型主筛)",
        "evidence": "按线型筛替代阈值筛后,竖向召回从 44% 升到 83%。",
    },
    "8.0.2": {
        "text": "轴号注写在轴线端部的圆内;圆用 0.25b 实线,直径 8~10mm;"
                "圆心应在定位轴线的延长线上;编号宜注写在平面图下方及左侧。",
        "applied_in": "core.model3d.axis_label_circle / axis_label_band",
        "evidence": "圈是独立 path、恰 4 条贝塞尔弧;实测直径 28.0pt = 9.88mm,"
                    "三张图检出 108/108、107/107、126/126。",
    },
    "8.0.3": {
        "text": "横向编号用阿拉伯数字,从左至右顺序编写;"
                "竖向编号用大写拉丁字母,从下至上顺序编写。",
        "applied_in": "core.model3d.axis_label_derive",
        "evidence": "实测统一规律:轴号递增 ⇔ 法向偏移递减,四个带(含两个旋转带)一致。",
    },
    "8.0.4": {
        "text": "I、O、Z 不得用作轴线编号;不够用时可用双字母或字母加数字注脚。",
        "applied_in": "本模块 AXIS_LETTERS / validate_axis_labels",
        "evidence": "A-01-02A 三个分区的字母序列实测均为 A…H, J…N, P, Q。",
    },
    "8.0.5": {
        "text": "复杂平面可分区编号,形式为「分区号-轴线号」;"
                "多子项时用「子项号-轴线号」。",
        "applied_in": "core.model3d.axis_zone_grouping",
        "evidence": "A-01-02A 实测三个分区(24+15 / 15+14 / 16+15);"
                    "同区横行带与竖列带间隙比 6~12%,跨区错配 86%。",
    },
    "8.0.6": {
        "text": "附加轴线编号用分数形式:分母为前一轴线号,分子为附加轴线序号;"
                "1 号或 A 号轴线之前的附加轴线,分母用 01 / 0A。",
        "applied_in": "core.model3d.axis_label_glyph(判 `/`)+ 本模块解析",
        "evidence": "实测 `2-1/k`、`2-1/11`、`2-1/C`;`/` 笔画长度比 0.47~0.48,"
                    "字母斜画 0.42,0.43~0.46 三张图全空。共 8 条。",
    },
    "8.0.7": {
        "text": "一个详图适用于多根轴线时,应同时注明各有关轴线的编号。",
        "applied_in": "(待落地)",
        "evidence": "",
    },
    "duplicate": {
        "text": "(工程约束,非条款)同一序列内轴号不得重复——"
                "重号会让两条轴线共用一个身份,跨图对齐必错。",
        "applied_in": "本模块 validate_axis_labels",
        "evidence": "",
    },
    "unparsable": {
        "text": "(工程约束,非条款)无法按 §8.0.3/§8.0.5/§8.0.6 解析的轴号,"
                "应如实报出而不是静默丢弃。",
        "applied_in": "本模块 validate_axis_labels",
        "evidence": "",
    },
}

_LABEL_RE = re.compile(
    r"^(?:(?P<zone>[0-9]+)-)?"                 # §8.0.5 分区前缀(可选)
    r"(?:"
    r"(?P<add_index>[0-9]+)/(?P<after>[0-9]+|[A-Z]+)"   # §8.0.6 分数式
    r"|(?P<value>[0-9]+|[A-Z]+)"                        # 主轴号
    r")$"
)


def purpose_of_line_type(line_type: str) -> str:
    """§4.0.2 线型 → 制图语义。"""
    return LINE_TYPE_PURPOSE.get(line_type, "未知")


def parse_axis_label(label: str) -> dict | None:
    """轴号字面 → 结构(§8.0.3 / §8.0.5 / §8.0.6);不合法返回 None。

    返回 {zone, value, kind, additional},其中 additional 为
    `{"index": n, "after": 前一轴号}` 或 None。
    """
    m = _LABEL_RE.match((label or "").strip().upper())
    if not m:
        return None

    if m.group("add_index"):
        after = m.group("after")
        if after.isalpha() and set(after) & FORBIDDEN_AXIS_LETTERS:
            return None
        return {
            "zone": m.group("zone"),
            "value": None,
            "kind": "numeric" if after.isdigit() else "alpha",
            "additional": {"index": int(m.group("add_index")), "after": after},
        }

    value = m.group("value")
    if value.isalpha() and set(value) & FORBIDDEN_AXIS_LETTERS:
        return None
    return {
        "zone": m.group("zone"),
        "value": value,
        "kind": "numeric" if value.isdigit() else "alpha",
        "additional": None,
    }


def _ordinal(value: str, kind: str) -> int | None:
    """轴号 → 序号,用于查缺号。字母按跳过 I·O·Z 的序列算。"""
    if kind == "numeric":
        return int(value)
    pool = _letter_pool(len(value) * len(AXIS_LETTERS) + len(AXIS_LETTERS))
    return pool.index(value) if value in pool else None


def _letter_pool(size: int) -> list[str]:
    pool: list[str] = []
    rep = 1
    while len(pool) < size:
        pool.extend(ch * rep for ch in AXIS_LETTERS)
        rep += 1
    return pool


def _violation(rule: str, detail: str) -> dict:
    return {"rule": rule, "detail": detail, "text": CLAUSES[rule]["text"]}


def validate_axis_labels(labels: list[str], *, kind: str) -> list[dict]:
    """按国标校验一条带内的轴号序列,返回违规列表(空表示合规)。

    检查:
    - **§8.0.3** 类型一致(数字向不能混字母)、序号连续(依次注写);
    - **§8.0.4** 不含 I、O、Z(由 `parse_axis_label` 直接拒绝);
    - **§8.0.5** 同一序列内分区号一致;
    - **§8.0.6** 附加轴线不占主序号,夹在中间不算缺号;
    - 重号与无法解析的轴号如实报出,不静默丢弃。

    **为什么要独立校验**:推导链本身不保证正确——实测踩过附加轴线混进主序列
    导致其后轴号整体偏移。校验能在出结果时就发现,而不是等人工看模型时才发现。
    """
    out: list[dict] = []
    parsed: list[dict] = []
    zones: set[str | None] = set()

    for label in labels:
        info = parse_axis_label(label)
        if info is None:
            out.append(_violation("unparsable", f"轴号 {label!r} 不合法"))
            continue
        zones.add(info["zone"])
        if info["additional"]:
            continue                       # §8.0.6 附加轴线不占主序号
        if info["kind"] != kind:
            out.append(_violation(
                "8.0.3", f"轴号 {label!r} 是 {info['kind']},与本带的 {kind} 不符"))
            continue
        parsed.append(info)

    if len(zones) > 1:
        out.append(_violation(
            "8.0.5", f"同一序列出现多个分区号:{sorted(z or '(无)' for z in zones)}"))

    seen: set[str] = set()
    ordinals: list[int] = []
    for info in parsed:
        value = info["value"]
        if value in seen:
            out.append(_violation("duplicate", f"轴号 {value!r} 重复"))
            continue
        seen.add(value)
        n = _ordinal(value, kind)
        if n is not None:
            ordinals.append(n)

    ordinals.sort()
    for a, b in zip(ordinals, ordinals[1:]):
        if b - a > 1:
            out.append(_violation(
                "8.0.3", f"序号 {a} 与 {b} 之间缺 {b - a - 1} 个,依次注写应连续"))
    return out
