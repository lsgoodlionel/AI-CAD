"""设计说明条文重组 —— **人看图的真正起点**。

人拿到一套图，先读设计总说明：分区范围、标高体系、材料等级、构造要求
都在那里写死。此前整条链路都在从几何猜，忽略了图纸自己已经用文字
说清楚的东西。

**实测规模**（两工程）：说明图 169 张、文字 61227 条，
其中**带条文编号 5166 条**，层级 1~4 级。

**前置问题**：PDF 按行提取，长条文被切碎（`1.3.1拉钢筋的抗`）。
而**编号是文档自带的结构** —— 一条编号开启一个条目，直到下一条编号
出现，中间的行都属于它。这比通用的「断行重组」更有依据。

**关键判据**：编号必须**单调递进**才算条文。否则表格里的 `1.5`（尺寸）
会被当成条文号，把上一条从中间切断。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 条文号：一到四级，`1` / `1.2` / `1.2.6` / `1.1.1.2`，
#: 后面必须跟分隔（空白、顿号、点）或直接接中文 —— `1.2.6地下室…`
#: 是实测里的常见写法（编号与正文之间没有空格）。
_CLAUSE_RE = re.compile(
    r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})\s*[、.．]?\s*"
    r"(?=[一-鿿（(])(.*)$")

#: 纯数字行（如 `3.600` 标高、`1.5` 尺寸）不是条文 —— 它后面没有中文正文。
#: 由 `_CLAUSE_RE` 的前瞻断言排除。


@dataclass
class SpecClause:
    """一条设计说明条文。"""
    number: str | None      # 条文号；首条之前的引言为 None
    text: str               # 正文（已把断行归并进来）
    level: int              # 层级：`1.2.6` 是 3
    parent: str | None      # 上级条文号


def parse_clause_number(line: str | None) -> tuple[str, str] | None:
    """行首是否为条文号 → (编号, 正文)；不是则 None。"""
    matched = _CLAUSE_RE.match(str(line or ""))
    if not matched:
        return None
    return matched.group(1), matched.group(2).strip()


def _parent_of(number: str) -> str | None:
    parts = number.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else None


def _advances(previous: str | None, candidate: str) -> bool:
    """编号是否**单调递进** —— 防表格数字被当成条文号。

    同级递增（1.2 → 1.3）、下沉一级（1.2 → 1.2.1）、
    回到上级并递增（1.2.6 → 1.3）都算递进；倒退不算。
    """
    if previous is None:
        return True
    prev = [int(p) for p in previous.split(".")]
    curr = [int(p) for p in candidate.split(".")]
    if len(curr) == len(prev) + 1 and curr[:-1] == prev:
        return True                              # 下沉一级
    depth = min(len(prev), len(curr))
    for i in range(depth):
        if curr[i] > prev[i]:
            return True                          # 该级递增
        if curr[i] < prev[i]:
            return False
    return len(curr) > len(prev)


def regroup_clauses(lines: list[str] | None) -> list[SpecClause]:
    """按行文本 → 条文列表（断行归并到所属条文）。"""
    clauses: list[SpecClause] = []
    preamble: list[str] = []
    current: SpecClause | None = None
    buffer: list[str] = []
    last_number: str | None = None

    def _flush() -> None:
        nonlocal current, buffer
        if current is not None:
            current.text = "".join(
                [current.text] + [b for b in buffer if b]).strip()
            clauses.append(current)
        current, buffer = None, []

    for raw in lines or []:
        line = str(raw or "").strip()
        if not line:
            continue
        parsed = parse_clause_number(line)
        if parsed is not None and _advances(last_number, parsed[0]):
            number, body = parsed
            if current is None and preamble:
                clauses.append(SpecClause(number=None,
                                          text="".join(preamble).strip(),
                                          level=0, parent=None))
                preamble = []
            _flush()
            current = SpecClause(number=number, text=body,
                                 level=number.count(".") + 1,
                                 parent=_parent_of(number))
            last_number = number
        elif current is not None:
            buffer.append(line)
        else:
            preamble.append(line)

    if current is None and preamble:
        clauses.append(SpecClause(number=None, text="".join(preamble).strip(),
                                  level=0, parent=None))
    _flush()
    return clauses


def regroup_clause_blocks(blocks: list[list[str]] | None) -> list[SpecClause]:
    """**按块**重组条文（每块独立重置单调性）。

    **为什么必须按块**（实测）：整图 80 行匹配条文号，
    全局单调性**拒了 72 行（90%）** —— 阅读顺序在图纸级别难以完全恢复，
    编号呈 `1, 1.1, 2.10.2, 3.3` 乱序。

    而单调性的本意是防「表格里的 `1.5` 被当条文号」，那是**块内**的问题；
    跨块比较没有依据 —— 两个说明块各自从 1 开始编号完全正常。
    """
    out: list[SpecClause] = []
    for block in blocks or []:
        out.extend(regroup_clauses(block))
    return out
