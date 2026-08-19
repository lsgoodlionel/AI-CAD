"""标题栏**字段标签**的识别（ISO 7200 / GB/T 10609.1）。

实测两个工程的档案层里，`other` 类别的高频内容全是标题栏表头：
`PROJECT` / `DATE` / `CLIENT` / `SCALE` / `DISCIPLINE`……
每张图各出现一次，合计近 4 万条。

它们是「PROJECT:」这个**标签**，不是项目名那个**值** —— 零信息量，
却把 `other` 的占比抬到 51% / 37%，掩盖了真正分类不出的内容。

**不删除**：标签的位置正是标题栏字段区域的锚点，
「图框字段区域记忆」要靠它们定位。只是不该混在 `other` 里。
"""
from __future__ import annotations

import re

#: ISO 7200《技术产品文件 标题栏》与 GB/T 10609.1《技术制图 标题栏》
#: 的标准字段名。**这些是国际/国家标准术语，不是某个设计院的写法** ——
#: 实测大歌剧院（ARCPLUS）与轨道交通（另一设计院）用的是同一套。
_LABELS_EN = {
    "project", "client", "date", "scale", "discipline", "status",
    "design", "drawing", "drawing title", "drawing no", "drawing number",
    "job", "job no", "job number", "no", "rev", "revision", "sheet",
    "approved", "checked", "drawn", "designed", "responsible", "seal",
    "title", "phase", "issue", "description", "reference",
}
_LABELS_ZH = {
    "项目名称", "工程名称", "建设单位", "设计单位", "设计号", "图号",
    "图别", "比例", "日期", "专业", "审定", "审核", "校对", "制图",
    "设计", "工种", "会签", "版次", "页次", "共页", "第页", "签字",
    # **会签栏的岗位名**（GB/T 10609.1 标题栏/会签栏）——
    # 实测「总」「责」「负」三字以单字形式各出现 360+ 次，
    # 正是「总工程师」「专业负责人」被竖排拆散的碎片。
    "总工程师", "专业负责人", "项目负责人", "负责人", "总负责",
    "技术负责人", "审定人", "设计人", "制图人", "校核",
}

#: 归一化：去首尾空白与标点、折叠内部空白、转小写。
_TRIM_RE = re.compile(r"^[\s:：.。、]+|[\s:：.。、]+$")
_SPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    body = _TRIM_RE.sub("", str(text or ""))
    return _SPACE_RE.sub(" ", body).strip().lower()


def is_title_block_label(text: str | None) -> bool:
    """这段文字是否为标题栏的**字段标签**（而非字段值）。"""
    body = _normalize(text)
    if not body:
        return False
    if body in _LABELS_EN:
        return True
    # 中文标签不转小写比较（大小写无意义），用原串去尾标点后比
    raw = _TRIM_RE.sub("", str(text or "")).strip()
    return raw in _LABELS_ZH


#: 标题栏中文标签的**用字集合** —— 由 `_LABELS_ZH` 拆出。
#: 实测大歌剧院 other 类别里，这些字各以单字形式出现 360~378 次
#: （≈ 图纸总数的 16%）：标题栏**竖排**文字被逐字提取，每字一条记录。
_LABEL_CHARS = {ch for label in _LABELS_ZH for ch in label}

#: **有意义的单字** —— 方位标记是真内容，不是碎片。
_MEANINGFUL_SINGLE = set("东西南北上下左右内外前后高低大小")


def is_label_fragment(text: str | None) -> bool:
    """是否为标题栏标签被竖排拆散后的**单字碎片**。

    只认「单个中文字 + 属于标签用字」：
    - 多字文本是真内容（`审核` / `比例` 本身就是完整标签，由
      `is_title_block_label` 处理）
    - ASCII 单字符可能是**轴号**（`A` / `1`），绝不归碎片
    - 方位字（东西南北）有意义，排除
    """
    body = str(text or "").strip()
    if len(body) != 1:
        return False
    if body in _MEANINGFUL_SINGLE:
        return False
    if not ("\u4e00" <= body <= "\u9fff"):    # 非中文（ASCII 轴号等）
        return False
    return body in _LABEL_CHARS
