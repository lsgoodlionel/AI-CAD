"""误检标签归一 —— 把同一件事的多种写法并成一个名字。

**为什么需要**：`what` 字段是判读者按**当批问法**填的，不同批次问法不同，
于是同一件事有三四种写法：

    墙        wall（英文批） · 墙（中文批） · no_wall（「是不是柱」批的否定式答案）
    空白      nothing · 空白 · no_empty · 单线 · line_only · sliver · 空白处单线
    梁        beam · beam_or_grid（管线批把梁与轴线并成一档）

不归一就没法回答「误检里有多少是墙」—— 而这正是唯一能指导改代码的问题。

**否定式写法的来路**：`column_outline` 批的问法是「这是不是柱」，
判读者答 `no_wall` / `no_empty` / `no_text`，意思是「不是柱，是墙／空白／文字」。
所以 `no_x` 一律归到 `x`，不是一个独立的类别。

**四个桶，按「该由谁去修」分**

| 桶 | 含义 | 该由谁修 |
|---|---|---|
| `real_component` | 误检物是另一种真实构件 | 构件级判据（真正的难题）|
| `drawing_level` | 图纸属性判错，这张图本就不该进构件识别 | 图纸级准入闸 |
| `annotation` | 图面标注、非实体图元 | 具名对象过滤器 |
| `judge_answer` | 该批 `what` 记的是判读侧答案，不是误检物 | 不该按误检读 |
| `unspecified` | 判读者没说清是什么 | 无法行动，只能重判 |

**未登记的写法不静默丢弃**：`taxonomy_summary` 会把它们单列在 `unmapped`
里点名。归一表靠人维护，靠人维护的表一定会落后于数据，所以它必须会喊。

**每个变体来自哪个批次不写死在这里**，而由 `report.py` 从数据现算
（`TaxonomySummary.sources`，报表「三、误检归一」一节逐条列出）。
写死会与数据漂移；现算的那份永远是真的。
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field

#: 桶名 → 中文说明。归一表里的每个标签都必须落在这里声明过的桶中。
BUCKETS = {
    "real_component": "别的真实构件",
    "drawing_level": "图纸级属性问题",
    "annotation": "图面标注/非实体",
    "judge_answer": "判读侧答案（非误检物）",
    "unspecified": "未具名",
}

#: 原始写法 → (归一标签, 桶)。
#:
#: 每一条的来路都能从数据里查到（`taxonomy_summary().sources`），
#: 这里只登记「哪些写法是同一件事」这个判断本身。
_TABLE: dict[str, tuple[str, str]] = {
    # ── 别的真实构件 ────────────────────────────────────────────────
    "wall": ("wall", "real_component"),
    "墙": ("wall", "real_component"),
    "no_wall": ("wall", "real_component"),
    # 判读者自己都分不开墙与梁时填的档，仍算「是别的构件」
    "wall_or_beam": ("wall", "real_component"),
    "beam": ("beam", "real_component"),
    # 管线批把「梁／轴线／结构线」并成一档 —— 主体是结构线，归梁
    "beam_or_grid": ("beam", "real_component"),
    "柱": ("column", "real_component"),
    # 设备批的 `structure` 实测全是柱与墙（见 equipment_v1 note）
    "structure": ("column", "real_component"),
    "seat": ("seat", "real_component"),
    "room": ("room_outline", "real_component"),
    "building_outline": ("room_outline", "real_component"),
    "楼面线": ("room_outline", "real_component"),
    "楼面或坡道": ("room_outline", "real_component"),
    "坡道": ("stair_ramp", "real_component"),
    "楼梯": ("stair_ramp", "real_component"),
    "栏杆": ("stair_ramp", "real_component"),
    "门": ("door_window", "real_component"),
    "door": ("door_window", "real_component"),
    "window": ("door_window", "real_component"),
    "opening": ("door_window", "real_component"),
    "家具设备": ("furniture_equipment", "real_component"),
    "furniture": ("furniture_equipment", "real_component"),
    "no_furniture": ("furniture_equipment", "real_component"),
    "equipment": ("furniture_equipment", "real_component"),
    "设备": ("furniture_equipment", "real_component"),
    "钢筋线": ("rebar", "real_component"),
    # **判据变过**：CRITERIA.md#columns v1 起「菱形姿态的柱仍算柱」，
    # 而这些裁决出自 v1 之前的批次，当时判为「不是柱」。留作独立标签，
    # 不并进 column —— 并进去等于用新判据改旧裁决。
    "旋转45度的菱形块": ("rotated_block_pre_v1", "real_component"),

    # ── 图纸级属性问题 ──────────────────────────────────────────────
    "not_spatial": ("not_spatial", "drawing_level"),
    "partial": ("partial_extent", "drawing_level"),
    "multi_floor": ("wrong_floor", "drawing_level"),
    "no_floor": ("wrong_floor", "drawing_level"),
    "too_short": ("scale_wrong", "drawing_level"),
    "too_long": ("scale_wrong", "drawing_level"),
    "way_off": ("scale_wrong", "drawing_level"),
    "no_discipline": ("no_discipline", "drawing_level"),
    "section_view": ("wrong_view_type", "drawing_level"),

    # ── 图面标注/非实体 ────────────────────────────────────────────
    "nothing": ("blank_or_single_line", "annotation"),
    "空白": ("blank_or_single_line", "annotation"),
    "no_empty": ("blank_or_single_line", "annotation"),
    "空白处单线": ("blank_or_single_line", "annotation"),
    "单线": ("blank_or_single_line", "annotation"),
    "line_only": ("blank_or_single_line", "annotation"),
    "sliver": ("blank_or_single_line", "annotation"),
    "标高三角": ("elevation_mark", "annotation"),
    "elevation_mark": ("elevation_mark", "annotation"),
    "leader": ("dimension_leader", "annotation"),
    "dimension": ("dimension_leader", "annotation"),
    "标注线": ("dimension_leader", "annotation"),
    "标注": ("dimension_leader", "annotation"),
    "text": ("text", "annotation"),
    "文字": ("text", "annotation"),
    "no_text": ("text", "annotation"),
    "hatch": ("hatch", "annotation"),
    "no_hatch": ("hatch", "annotation"),
    "填充线": ("hatch", "annotation"),
    "axis": ("axis_line", "annotation"),
    "轴线": ("axis_line", "annotation"),
    "轴线刻度": ("axis_line", "annotation"),
    "图框": ("frame_titleblock", "annotation"),
    "图签栏": ("frame_titleblock", "annotation"),
    # 索引小图／关键平面：图面上的另一张微缩图，不是构件
    "缩略图": ("key_plan_thumbnail", "annotation"),

    # ── 判读侧答案（不是误检物）───────────────────────────────────
    # `axis_grid_presence` 问「这张图有没有轴网」，`what` 记的是判读者的答案，
    # 系统答错时 ok=False。按误检物去读会得出「误检了 4 个 yes_full」这种胡话。
    "yes_full": ("judge_said_yes", "judge_answer"),
    "no": ("judge_said_no", "judge_answer"),

    # ── 未具名 ─────────────────────────────────────────────────────
    "unsure": ("unsure", "unspecified"),
    "no_other": ("other", "unspecified"),
}


def normalize_table() -> dict[str, tuple[str, str]]:
    """归一表副本（原始写法 → 归一标签 + 桶）。"""
    return dict(_TABLE)


def canonical_what(raw: str) -> str | None:
    """原始写法 → 归一标签；未登记返回 ``None``（**不猜**）。"""
    return _TABLE.get(str(raw or "").strip(), (None, None))[0]


def bucket_of(raw: str) -> str | None:
    """原始写法 → 桶名；未登记返回 ``None``。"""
    return _TABLE.get(str(raw or "").strip(), (None, None))[1]


@dataclass
class TaxonomySummary:
    """误检归一汇总。"""

    total: int = 0
    #: 归一标签 → 条数
    labels: dict = field(default_factory=dict)
    #: 桶名 → 条数
    buckets: dict = field(default_factory=dict)
    #: 归一标签 → {原始写法: 条数}
    variants: dict = field(default_factory=dict)
    #: 原始写法 → 来源批次文件名集合
    sources: dict = field(default_factory=dict)
    #: 未登记的原始写法 → 条数。**非空即表示归一表落后于数据**
    unmapped: dict = field(default_factory=dict)

    def bucket_share(self, bucket: str) -> float:
        return self.buckets.get(bucket, 0) / self.total if self.total else 0.0


def summarize(pairs) -> TaxonomySummary:
    """``pairs`` 是 ``(批次文件名, 原始 what)`` 序列（只喂 ok=False 的裁决）。"""
    s = TaxonomySummary()
    labels: collections.Counter = collections.Counter()
    buckets: collections.Counter = collections.Counter()
    variants: dict = collections.defaultdict(collections.Counter)
    sources: dict = collections.defaultdict(set)
    unmapped: collections.Counter = collections.Counter()

    for batch, raw in pairs:
        raw = str(raw or "").strip()
        s.total += 1
        sources[raw].add(batch)
        hit = _TABLE.get(raw)
        if hit is None:
            unmapped[raw] += 1
            labels["<未登记>"] += 1
            buckets["unspecified"] += 1
            variants["<未登记>"][raw] += 1
            continue
        label, bucket = hit
        labels[label] += 1
        buckets[bucket] += 1
        variants[label][raw] += 1

    s.labels = dict(labels.most_common())
    s.buckets = dict(buckets.most_common())
    s.variants = {k: dict(v.most_common()) for k, v in variants.items()}
    s.sources = {k: set(v) for k, v in sources.items()}
    s.unmapped = dict(unmapped.most_common())
    return s
