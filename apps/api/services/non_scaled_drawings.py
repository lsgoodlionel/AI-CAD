"""不按比例绘制的图纸识别 —— 这类图**不应有坐标变换**。纯函数。

## 领域知识(用户指出)

1. **N.T.S**(Not To Scale):如「给排水-雨水系统原理图」标注 N.T.S,
   **不按比例绘制**,仅表示系统原理;
2. **文字类图纸**:图纸目录、总说明、设计说明、做法表/材料表等,
   **本身没有比例**,只需理解文字内容。

## 为什么必须识别

这类图上出现的 `1:N` 往往是**别的图纸的比例**(如目录里列出的各图比例),
误当作本图比例会产生**错误坐标变换** → 构件位置全错。
实测:批量确认曾给「建筑-竣工图--图纸目录」赋予 1:350 / 1:50 变换(错误)。
"""
from __future__ import annotations

import re

#: N.T.S 标注(Not To Scale)的多种写法
_NTS_PATTERN = re.compile(r"\bN\.?\s*T\.?\s*S\.?\b", re.IGNORECASE)
_NTS_CN = ("不按比例", "无比例", "非比例")

#: 文字类/示意类图纸标题关键词——本身不按比例
NON_SCALED_TITLE_KEYWORDS = (
    "目录", "总说明", "设计说明", "施工说明", "说明书",
    "做法表", "材料表", "用料", "选用表", "配置表",
    # 注:不含「构造做法」——「构造做法**图**」是画出来的详图,通常按比例绘制,
    # 误判会丢掉有效变换(实测该词仅命中 6 张,全是详图)。
    "原理图", "系统图", "示意图", "流程图", "框图", "系统原理",
    "图例", "图签", "封面", "签署", "会签",
    "计算书", "统计表", "清单", "汇总表",
)


def has_nts_marker(texts: list[str]) -> bool:
    """图纸文字中是否出现 N.T.S / 不按比例 标注。"""
    for text in texts or []:
        t = str(text)
        if _NTS_PATTERN.search(t) or any(k in t for k in _NTS_CN):
            return True
    return False


def is_non_scaled_title(title: str | None) -> bool:
    """图名是否属于文字类/示意类(本身没有比例)。"""
    name = str(title or "")
    return any(k in name for k in NON_SCALED_TITLE_KEYWORDS)


def is_non_scaled(title: str | None, texts: list[str] | None = None) -> tuple[bool, str]:
    """该图是否**不按比例绘制** → (是否, 原因)。

    命中任一即判定:① 图名属文字/示意类;② 图上标注 N.T.S/不按比例。
    """
    if is_non_scaled_title(title):
        hit = next(k for k in NON_SCALED_TITLE_KEYWORDS if k in str(title or ""))
        return True, f"图名含「{hit}」,属文字/示意类图纸,本身无比例"
    if has_nts_marker(texts or []):
        return True, "图上标注 N.T.S(不按比例绘制,仅表示原理)"
    return False, ""
