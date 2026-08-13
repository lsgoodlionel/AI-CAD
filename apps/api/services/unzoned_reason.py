"""未分层图的**原因分类** —— 让人工队列可操作（J7 设计答案 2）。

人工通道早就有（`drawing_model_annotations` 可写 `story_key`/`building_unit_key`），
缺的是**分类**：队列里 1061 张，人打开看不出每张为什么判不出、该补什么。

**三类要分开，因为人的动作不同**：

| 类别 | 人该做什么 |
|---|---|
| 跨楼层 | **什么也不做** —— 它本就不属于单一楼层，硬指定反而错 |
| 非标准楼层名 | 告诉系统「台仓 ≈ 哪一层」，不必翻图 |
| 毫无线索 | 翻图确认 |

判据来自**内容**（国标术语与工程惯用词），不绑任何院的编号体系
（见 `docs/MODELING_PIPELINE_BLUEPRINT.md` §7 约束 1、2）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

#: 本就不属于单一楼层的图 —— 竖向定位、立面分格等跨楼层表达。
REASON_CROSS_FLOOR = "cross_floor"
#: 有楼层含义但不是标准楼层名（台仓、夹层、天桥…）。
REASON_NON_STANDARD_NAME = "non_standard_floor_name"
#: 图上找不到任何楼层线索。
REASON_NO_HINT = "no_floor_hint"
#: **本就不该有楼层**的图（说明、目录、系统图、详图）。
#:
#: 实测未分层 1061 张里 93.6% 落在「毫无线索」兜底类，而队列第一条是
#: 「01施工总说明」—— 它本就没有楼层。**「本就没有」与「该有却没有」
#: 混在一起报，会让人去处理一个不存在的问题**
#: （`building_unit_fallback` 那轮实测虚高 2.1 倍）。
REASON_NO_FLOOR_BY_NATURE = "no_floor_by_nature"

#: 跨楼层表达的术语。「竖向」是国标里表达跨层构件的通用词。
_CROSS_FLOOR_RE = re.compile(r"竖向|立面分格|展开图|竖井|管井大样")

#: 有楼层含义但非标准名。**台仓**是舞台下方空间，大歌剧院实测出现。
_NON_STANDARD_RE = re.compile(r"台仓|台塔|夹层|马道|天桥|检修层|设备夹层")


@dataclass(frozen=True)
class UnzonedReason:
    """一条未分层原因。`action` 是给人看的建议动作。"""

    reason: str
    action: str
    #: 是否需要人填楼层。跨楼层图为 False —— 让人填反而会填错。
    needs_floor_input: bool
    #: 识别到的非标准名，回显给人看（如「台仓」）。
    hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "action": self.action,
                "needs_floor_input": self.needs_floor_input, "hint": self.hint}


def classify_unzoned(drawing: Mapping[str, Any] | None) -> UnzonedReason:
    """未分层的图 → 原因分类。

    **逐源匹配**（title → drawing_no）：拼成一串再匹配会让行尾锚点失效，
    这是 `drawing_role._by_term` 已经踩过的坑。
    """
    sources = [str((drawing or {}).get(key) or "").strip()
               for key in ("title", "drawing_no", "filename")]

    for text in sources:
        if text and _CROSS_FLOOR_RE.search(text):
            return UnzonedReason(
                REASON_CROSS_FLOOR,
                "本图跨多个楼层（如竖向定位图），**不属于单一楼层**；"
                "无需指定楼层，可直接在图纸管理里标为「跨层图」。",
                needs_floor_input=False,
                hint=_CROSS_FLOOR_RE.search(text).group(0))

    for text in sources:
        match = text and _NON_STANDARD_RE.search(text)
        if match:
            name = match.group(0)
            return UnzonedReason(
                REASON_NON_STANDARD_NAME,
                f"图名含「{name}」——有楼层含义但不是标准楼层名。"
                f"请告知系统「{name}」对应哪一层（或哪个标高区间），"
                "之后重跑即可自动归层。",
                needs_floor_input=True, hint=name)

    # **本就无楼层的图**排在兜底之前：说明/目录/系统图/详图不该进人工队列。
    # 判据复用 `drawing_role`（国标术语，不绑任何院的编号体系）。
    try:
        from services.drawing_role import (
            ROLE_DETAIL, ROLE_NON_GEOMETRIC, classify_role,
        )

        role = classify_role(drawing or {}).role
        if role in (ROLE_NON_GEOMETRIC, ROLE_DETAIL):
            return UnzonedReason(
                REASON_NO_FLOOR_BY_NATURE,
                "本图按其类型（说明/目录/系统图/详图）**本就不属于某一楼层**，"
                "无需分层，也不必进人工队列。",
                needs_floor_input=False, hint=role)
    except Exception:  # noqa: BLE001 — 判不了就落到兜底，不阻断
        pass

    return UnzonedReason(
        REASON_NO_HINT,
        "图上未找到楼层线索（图名、标高链、图框栏均无）。"
        "需人工翻图确认所属楼层后补录。",
        needs_floor_input=True)
