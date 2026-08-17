"""轴号推导:按国标编写顺序从带内位置推出轴号,OCR 只用于锚定。

**为什么是推导而不是识别**:圈内轴号是**一根一根线段画出来的字形**(CAD 出图特色),
不是文字。实测 RapidOCR 在 8 种配置(300/600/900dpi × 含圈/圈内 × 形态学加粗 0/5)
下最好只有 **1/24** 完全命中——OCR 模型没见过这种发丝笔画字形,这条路走不通。

**走得通的路**是 GB/T 50001 §8.0.3:

    「横向编号应用阿拉伯数字,**从左至右**顺序编写;
      竖向编号应用大写拉丁字母,**从下至上**顺序编写。」

带内成员已按位置排好序(见 `axis_label_band`),所以轴号是**可推导的**。
配合 §8.0.4(跳过 I、O、Z)即可直接生成序列。

**实测验证的统一规律**——轴号递增 ⇔ 法向偏移递减,四个带(含两个旋转带)一致:

    分区1 数字 1-1→1-24   偏移  -992 → -2522
    分区2 字母 2-A→2-P    偏移  1529 →   778
    分区3 数字 3-1→3-16   偏移 -1653 → -2530   (132° 旋转带)
    分区3 字母 3-A→3-Q    偏移  -434 → -1182   (42° 旋转带)

之所以两类都是「递减」:90° 的法向是 (-1,0)(x 越大偏移越小),
0° 的法向是 (0,1),而 PDF 的 y 轴向下(越靠下 y 越大)。

**OCR 的正确用法**:不去读全名,只在能读出的那几个位置上**锚定序列起点**
并交叉校验。实测 24 个圈中 OCR 读出 10 个数字且位置全部对上,足以锚定。

**诚实边界**:分区号(§8.0.5 的「分区号-轴线号」)无法从几何推出,
必须由调用方给定或人工确认;推不出时就不加前缀,不猜。
"""
from __future__ import annotations

from services.axis_label_sequence import ALPHA_AXIS

NUMERIC_KIND = "numeric"
ALPHA_KIND = "alpha"

#: 锚定「可信」所需的最少吻合数。单点吻合可能是巧合
MIN_ANCHOR_AGREEMENTS = 2

#: 锚定时搜索的位移范围。带一般从 1 或 A 开始,偏移不会很大
ANCHOR_SHIFT_RANGE = 40


def label_kind_for_axis_angle(angle_deg: float) -> str:
    """轴线方向 → 该族用数字还是字母(§8.0.3)。

    竖向轴线用数字、横向轴线用字母。旋转分区按**更靠近哪个正交方向**归类
    ——实测分区 3 的 132° 轴线编数字、42° 轴线编字母,与该规则一致。

    45° 时两边等距,取 ALPHA 以保证结果稳定(不随浮点抖动翻转)。
    """
    a = angle_deg % 180.0
    to_vertical = abs(a - 90.0)
    to_horizontal = min(a, 180.0 - a)
    return NUMERIC_KIND if to_vertical < to_horizontal else ALPHA_KIND


def order_axes_for_labelling(axes: list[dict]) -> list[dict]:
    """按编写顺序排序:**轴号递增 ⇔ 法向偏移递减**(不改入参)。"""
    return sorted(axes, key=lambda a: -a["offset_pt"])


def _sequence(kind: str, count: int, start: str | None) -> list[str]:
    if kind == NUMERIC_KIND:
        begin = int(start) if start else 1
        return [str(begin + i) for i in range(count)]
    pool: list[str] = []
    rep = 1
    while len(pool) < count + len(ALPHA_AXIS):
        pool.extend(ch * rep for ch in ALPHA_AXIS)
        rep += 1
    offset = pool.index(start.strip().upper()) if start else 0
    return pool[offset:offset + count]


def derive_band_labels(axes: list[dict], *, zone: str | None,
                       start: str | None = None) -> list[dict]:
    """带内轴线 → 带轴号的轴线(纯函数)。

    zone 为 None 时**不加分区前缀**——分区号推不出来,不能瞎猜一个。
    """
    if not axes:
        return []
    ordered = order_axes_for_labelling(axes)
    kind = label_kind_for_axis_angle(ordered[0]["angle_deg"])
    names = _sequence(kind, len(ordered), start)
    prefix = f"{zone}-" if zone else ""
    return [
        {**axis, "label": f"{prefix}{name}", "label_kind": kind,
         "label_source": "derived"}
        for axis, name in zip(ordered, names)
    ]


def derive_zone_labels(axes: list[dict], *, zone: str | None,
                       starts: dict[str, str] | None = None) -> list[dict]:
    """分区内轴线 → 带轴号的轴线。

    **必须按方向分开推导**:一个分区同时含数字向与字母向轴线,
    `derive_band_labels` 只看第一条轴线的方向,混在一起会让整批轴号错位
    (实测 39 条轴线全被当成一种,24 个数字标签全错)。

    starts 可按类型指定起始轴号,如 {"numeric": "5"}。
    """
    if not axes:
        return []
    starts = starts or {}
    by_kind: dict[str, list[dict]] = {}
    for axis in axes:
        by_kind.setdefault(
            label_kind_for_axis_angle(axis["angle_deg"]), []).append(axis)
    out: list[dict] = []
    for kind, group in by_kind.items():
        out.extend(derive_band_labels(group, zone=zone, start=starts.get(kind)))
    return out


def _normalized(text: str) -> str:
    return (text or "").strip().upper().replace(" ", "")


def anchor_from_reads(derived: list[str], reads: dict[int, str]) -> dict:
    """用 OCR 读出的零散片段锚定序列起点。

    derived 是按位置推出的轴号序列(不含分区前缀);
    reads 是 {带内位置下标: OCR 读到的文本}。

    返回 {shift, agreements, conflicts, confident}。shift 是应施加于
    derived 的位移(读到 5 而推导为 1 → shift 4)。**冲突照实报出**,
    不静默丢弃——实测有位置被误读成别的数字。
    """
    clean = {i: _normalized(t) for i, t in reads.items()
             if 0 <= i < len(derived) and _normalized(t)}
    if not clean:
        return {"shift": 0, "agreements": 0, "conflicts": 0, "confident": False}

    best = None
    for shift in range(-ANCHOR_SHIFT_RANGE, ANCHOR_SHIFT_RANGE + 1):
        agree = conflict = 0
        for i, text in clean.items():
            expected = _shifted(derived, i, shift)
            if expected is None:
                # 该位移把这个位置推到了非法轴号(如 0 或负数)。这是**该位移的
                # 失败**,不能跳过——否则极端位移可以靠「让不方便的读数消失」取胜,
                # 实测会选出 shift=-22 这种荒谬解。
                conflict += 1
            elif expected == text:
                agree += 1
            else:
                conflict += 1
        # 吻合多者优先;并列时取冲突少者,再并列取位移小者,保证输出稳定
        key = (-agree, conflict, abs(shift))
        if best is None or key < best[0]:
            best = (key, {"shift": shift, "agreements": agree,
                          "conflicts": conflict,
                          "confident": agree >= MIN_ANCHOR_AGREEMENTS})
    return best[1]


def _shifted(derived: list[str], index: int, shift: int) -> str | None:
    """位移后该位置本应是什么轴号。数字可直接加减,字母按序列表查。"""
    base = derived[index]
    if base.isdigit():
        value = int(base) + shift
        return str(value) if value >= 1 else None
    pool: list[str] = []
    rep = 1
    while len(pool) < len(derived) + ANCHOR_SHIFT_RANGE + len(ALPHA_AXIS):
        pool.extend(ch * rep for ch in ALPHA_AXIS)
        rep += 1
    if base not in pool:
        return None
    target = pool.index(base) + shift
    return pool[target] if 0 <= target < len(pool) else None
