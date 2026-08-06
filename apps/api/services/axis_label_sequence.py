"""轴号序列展开与方向排序(人工批量标定轴线的纯逻辑层)。

人工标定的第二种形式:一次框选多条直线作为轴线,只说「起始轴号 + 终止轴号 +
命名方向」,由系统自动把标签按顺序派给每条线。本模块只做这件事,不碰 IO,
可离线测。

轴号约定(GB/T 50001 制图标准):
- 竖向轴线(direction='x')用数字 1、2、3…
- 横向轴线(direction='y')用大写字母,**跳过 I、O、Z**(易与 1、0、2 混淆),
  用尽后用 AA、BB、CC…
- **附加轴号**(分轴线)形如 `1-1`、`2-10`、`A-1`、`B-20`:主轴号 + 分隔符 + 序号。
  实际工程图里很常见,顺推时**只递增分号部分**,主轴号不动
  (`1-1`→`1-2`,而非 `1-1`→`2-1`)。
"""
from __future__ import annotations

import re

from core.model3d.drawing_conventions import AXIS_LETTERS

# 跳过 I / O / Z 的字母轴号序列(制图标准硬要求)
# §8.0.4 跳过 I·O·Z 的字母轴号序列。单一来源在 `drawing_conventions`,
# 此前这里与那边各写一遍,改一处就会漂移
ALPHA_AXIS = AXIS_LETTERS

#: 命名方向 → (排序取的坐标轴, 是否升序)。归一化坐标 y 向下为正,故
#: 「从上到下」= y 升序。
DIRECTION_ORDER: dict[str, tuple[str, bool]] = {
    "left_to_right": ("x", True),
    "right_to_left": ("x", False),
    "top_to_bottom": ("y", True),
    "bottom_to_top": ("y", False),
}

_MAX_SEQUENCE = 400  # 单次批量标定上限,防误框选刷爆

#: 附加轴号:主轴号(数字或字母) + 分隔符(- 或 /) + 序号
_SUB_AXIS_RE = re.compile(r"^([0-9]+|[A-Z]+)\s*([-/])\s*([0-9]+)$")


def _alpha_sequence(n: int) -> list[str]:
    """A、B…Y(跳 I/O/Z),用尽后 AA、BB…"""
    out: list[str] = []
    rep = 1
    while len(out) < n:
        out.extend(ch * rep for ch in ALPHA_AXIS)
        rep += 1
    return out[:n]


def parse_sub_axis(label: str) -> tuple[str, str, int] | None:
    """附加轴号 → (主轴号, 分隔符, 序号);不是附加轴号返回 None。

    `1-1` → ("1","-",1) · `A/3` → ("A","/",3) · `B-20` → ("B","-",20)
    """
    m = _SUB_AXIS_RE.match((label or "").strip().upper())
    if not m:
        return None
    base, sep, sub = m.group(1), m.group(2), int(m.group(3))
    if base.isdigit():
        return base, sep, sub
    if len(set(base)) == 1 and base[0] in ALPHA_AXIS:
        return base, sep, sub
    return None          # 主轴号非法(如 I/O/Z 或混合字符)


def label_kind(label: str) -> str:
    """轴号字面 → 'numeric' | 'alpha' | 'sub'(附加轴号);都不是则 'unknown'。"""
    s = (label or "").strip().upper()
    if not s:
        return "unknown"
    if s.isdigit():
        return "numeric"
    if len(set(s)) == 1 and s[0] in ALPHA_AXIS:
        return "alpha"
    if parse_sub_axis(s):
        return "sub"
    return "unknown"


def expand_labels(start: str, end: str | None, count: int) -> list[str]:
    """起止轴号 → 长度 count 的轴号序列。

    end 为空时按 start 的类型顺推 count 个;给了 end 则**校验条数**——
    条数对不上说明选线选错了,宁可报错也不静默错配轴号(错配比不标更糟)。
    """
    if count <= 0:
        raise ValueError("未选中任何轴线")
    if count > _MAX_SEQUENCE:
        raise ValueError(f"单次批量标定上限 {_MAX_SEQUENCE} 条,当前 {count} 条")

    kind = label_kind(start)
    if kind == "unknown":
        raise ValueError(
            f"起始轴号 {start!r} 非法:应为数字(1)、字母(A)或附加轴号(1-1 / A-2)")

    if kind == "sub":
        base, sep, first = parse_sub_axis(start)
        seq = [f"{base}{sep}{first + i}" for i in range(count)]
    elif kind == "numeric":
        begin = int(start)
        seq = [str(begin + i) for i in range(count)]
    else:
        pool = _alpha_sequence(count + len(ALPHA_AXIS) * 3)
        s = start.strip().upper()
        if s not in pool:
            raise ValueError(f"起始轴号 {start!r} 不在字母轴号序列内")
        i0 = pool.index(s)
        seq = pool[i0:i0 + count]

    if end:
        want = end.strip().upper().replace(" ", "")
        if label_kind(end) != kind:
            raise ValueError(f"起止轴号类型不一致:{start} / {end}")
        if kind == "sub":
            end_base = parse_sub_axis(end)[0]
            if end_base != parse_sub_axis(start)[0]:
                raise ValueError(
                    f"附加轴号的主轴号须一致:{start} / {end}")
        if seq[-1] != want:
            raise ValueError(
                f"选中 {count} 条线对应轴号 {seq[0]}~{seq[-1]},"
                f"与填写的终止轴号 {want} 不符;请核对选线条数"
            )
    return seq


def order_lines(lines: list[dict], direction_order: str) -> list[dict]:
    """按命名方向给选中的线排序(不改原列表,返回新列表)。"""
    if direction_order not in DIRECTION_ORDER:
        raise ValueError(f"未知命名方向 {direction_order!r}")
    axis, ascending = DIRECTION_ORDER[direction_order]
    key = (lambda ln: (ln["x1_norm"] + ln["x2_norm"]) / 2) if axis == "x" \
        else (lambda ln: (ln["y1_norm"] + ln["y2_norm"]) / 2)
    return sorted(lines, key=key, reverse=not ascending)


def assign_labels(
    lines: list[dict], *, start: str, end: str | None,
    direction: str, direction_order: str,
    spacing_mm: list[float] | None = None,
) -> list[dict]:
    """选中的多条线 + 起止轴号 + 命名方向 → 可直接入库的轴线基准列表。

    spacing_mm 可选:按排序后顺序给出相邻轴距(第 1 条无前一条,故长度 count-1),
    用于反算比例尺。
    """
    ordered = order_lines(lines, direction_order)
    labels = expand_labels(start, end, len(ordered))
    refs: list[dict] = []
    for i, (ln, label) in enumerate(zip(ordered, labels)):
        spacing = None
        if spacing_mm and 0 < i <= len(spacing_mm):
            spacing = spacing_mm[i - 1]
        refs.append({
            "label": label,
            "direction": direction,
            "x1_norm": ln["x1_norm"], "y1_norm": ln["y1_norm"],
            "x2_norm": ln["x2_norm"], "y2_norm": ln["y2_norm"],
            "spacing_to_prev_mm": spacing,
        })
    return refs
