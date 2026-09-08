"""金标准常设报表 —— 把度量口径固定成代码，而不是每次现写脚本。

现写的脚本口径不留痕，于是同一个「柱」被算出过 **0.59 / 0.22 / 68% / 22%**
四个数字，事后才发现是四套口径。本模块把三条口径写死：

**一、分组必须拆开。**
对照组（`blank_control`，验的是仪器可信不可信）与被闸删掉组
（`dropped_by_gate`，验的是闸删对没删对）里，「对」的含义与主组**相反** ——
对照组答对是「没把空白判成柱」。混算出来的数没有意义：
`columns_final` 混算 60%，而真实的 kept 组只有 **12%**。

**二、两极分化按图纸算，不按瓦片算。**
「一张图要么整张能识别、要么整张不能」是全部数据里最强的信号，
但它是**图纸级**的。`verdicts_v1` 的单元是瓦片，109 个单元只落在 39 张图上；
按单元算得 11 张 / 82%，按图纸算是 20 张 / 35%（`docs/GOLD_STANDARD_REVIEW.md`
用的是前者）。默认按图纸，`by="unit"` 保留下来以复现旧口径。

**三、没有精确率的类要说「不适用」，不能报 0%。**
`count` / `text` / `fields` / `instances` 记的是计数与字段。
报 0% 会让读者以为「测出来是零」。

误检标签的归一表在 `taxonomy.py`。

---

## 与 `docs/GOLD_STANDARD_REVIEW.md` 对不上的三处

报表跑通后逐格核对，总数（1096 条裁决、559 条误检）与盘点文档一致，
逐类真阳率也全部一致。**对不上的是三处派生数字，三处都是文档错**：

1. **盘点表漏了 `walls`（walls_v1）一行。** 表里 16 行加起来 1049 条，
   而总数 1096 —— 差的正是 walls 的 47 条（主组 33/47 = 70%）。
   总数没错，表不全；照那张表求和会少 47 条。

2. **`columns` 的两极分化 82% 是瓦片级，不是图纸级。**
   `verdicts_v1` 的单元是**瓦片**，109 个单元只落在 39 张图上。
   按单元算：11 张 ≥2 格、7 全对 2 全错 → 82%；
   按图纸算：20 张 ≥2 格、4 全对 3 全错 → **35%**。
   连带地，六个类的两极占比均值从 **80% 降到 72%**
   （100/85/79/69/62/35）。结论方向不变（两极分化仍是最强信号），
   但「平均 80%」这个数要改。

3. **误检三分的占比与归一后的口径不一致。**
   文档写 44% / 24% / 22%（合计 90%，剩下 10% 没有交代）；
   归一表全覆盖后是 **47% / 24% / 25%**（`real_component` 265 ·
   `drawing_level` 133 · `annotation` 142，另有 `unspecified` 13、
   `judge_answer` 6）。差别来自三处归属：`section_view`(2) 归了图纸级、
   `标注`(4)/`no_text`(2)/`no_hatch`(2)/`填充线`(1) 归了标注、
   `图框图签栏`(6)/`轴线`(9)/`缩略图`(3) 此前没进任何一档。
   另外文档那个 22% 本身也偏高：它列举的五项合计 117 条 = **20.9%**。
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field

from .schema_check import RAW_SHEETS, check_gold_dir, load_gold_files
from .taxonomy import BUCKETS, TaxonomySummary, summarize

#: 分组名里出现这些词，说明该组不是主测对象，不并进主口径。
#: 它们的「对」与主组含义相反（对照组答对＝没把空白判成构件）。
AUXILIARY_GROUP_MARKS = ("control", "dropped")

#: 一张图至少要有这么多格才进两极分化统计。
#: 一格的图天然「全对」或「全错」，算进去等于自证结论。
MIN_CELLS_PER_DRAWING = 2

#: 判据字段以此开头 = 本批判据未固化，回指不算数。
_UNFIXED = "UNFIXED"


def _is_auxiliary(group: str) -> bool:
    g = (group or "").lower()
    return any(mark in g for mark in AUXILIARY_GROUP_MARKS)


def _group_of(unit_raw: dict) -> str:
    src = unit_raw.get("source") or {}
    return str(src.get("group") or src.get("stratum") or "")


def _drawing_key(unit_raw: dict, by: str) -> str:
    """两极分化的分组键。"""
    uid = str(unit_raw.get("unit", "?"))
    if by == "unit":
        return uid
    src = unit_raw.get("source") or {}
    return str(src.get("drawing_id") or src.get("tile") or uid)


@dataclass
class GroupRow:
    """一个抽样分组的数字。"""

    name: str
    verdicts: int = 0
    ok: int = 0
    auxiliary: bool = False

    @property
    def rate(self) -> float | None:
        return self.ok / self.verdicts if self.verdicts else None


@dataclass
class ClassReport:
    """一个（文件, 对象类）的汇总。"""

    file: str
    object_class: str
    method: str
    units: int = 0
    verdicts: int = 0
    ok: int = 0
    main_verdicts: int = 0
    main_ok: int = 0
    groups: list = field(default_factory=list)
    #: 判据回指原文（多个单元不一致时取出现过的全部）
    criteria: list = field(default_factory=list)

    @property
    def has_rate(self) -> bool:
        """有没有精确率。`count`/`text`/`fields`/`instances` 没有。"""
        return self.method == "verdicts" and self.verdicts > 0

    @property
    def main_rate(self) -> float | None:
        """主组真阳率 —— 唯一可与别的批次比较的数。"""
        if not self.has_rate or not self.main_verdicts:
            return None
        return self.main_ok / self.main_verdicts

    @property
    def overall_rate(self) -> float | None:
        """含对照组与被闸删掉组的混算数。**留作对照，不要引用。**"""
        if not self.has_rate:
            return None
        return self.ok / self.verdicts

    @property
    def has_criteria(self) -> bool:
        """回指了 CRITERIA.md 的真实小节（`UNFIXED:` 标注不算）。"""
        return any(c and not c.upper().startswith(_UNFIXED)
                   for c in self.criteria)

    @property
    def criteria_unfixed(self) -> bool:
        return bool(self.criteria) and not self.has_criteria


@dataclass
class PolarizationRow:
    """一个对象类的图纸级两极分化。"""

    object_class: str
    drawings: int = 0
    all_ok: int = 0
    all_bad: int = 0
    mixed: int = 0
    #: 分组键的真实来源：`drawing`（图纸号/瓦片）或 `unit`（退化到单元）
    resolution: str = "drawing"

    @property
    def polarized(self) -> int:
        return self.all_ok + self.all_bad

    @property
    def share(self) -> float | None:
        return self.polarized / self.drawings if self.drawings else None


@dataclass
class GoldReport:
    """整套金标准的常设指标。"""

    total_files: int = 0
    total_units: int = 0
    total_verdicts: int = 0
    total_ok: int = 0
    classes: list = field(default_factory=list)
    polarization_by_drawing: dict = field(default_factory=dict)
    polarization_by_unit: dict = field(default_factory=dict)
    taxonomy: TaxonomySummary = field(default_factory=TaxonomySummary)
    issues: list = field(default_factory=list)
    raw_sheets: dict = field(default_factory=dict)

    @property
    def classes_with_criteria(self) -> int:
        return sum(1 for c in self.classes if c.has_criteria)

    @property
    def classes_without_criteria(self) -> int:
        return sum(1 for c in self.classes if not c.has_criteria)

    @property
    def criteria_coverage(self) -> float:
        return (self.classes_with_criteria / len(self.classes)
                if self.classes else 0.0)


def _iter_classes(files):
    """遍历 (文件名, 单元原始 dict, 类名, 类原始 dict)。"""
    for f in files:
        for unit in (f["data"].get("units") or []):
            for cls_name, cls in (unit.get("classes") or {}).items():
                yield f["name"], unit, cls_name, cls


def class_reports(files=None) -> list[ClassReport]:
    """按（文件, 对象类）汇总。同名类出现在多个文件里时分行，不合并。"""
    files = load_gold_files() if files is None else files
    reports: dict[tuple, ClassReport] = {}
    groups: dict[tuple, dict] = collections.defaultdict(dict)

    for fname, unit, cls_name, cls in _iter_classes(files):
        key = (fname, cls_name)
        rep = reports.get(key)
        if rep is None:
            rep = reports[key] = ClassReport(
                file=fname, object_class=cls_name,
                method=str(cls.get("method") or ""))
        rep.units += 1
        criteria = cls.get("criteria")
        if criteria and criteria not in rep.criteria:
            rep.criteria.append(criteria)

        verdicts = cls.get("verdicts") or []
        if not verdicts:
            continue
        ok = sum(1 for v in verdicts if v.get("ok"))
        rep.verdicts += len(verdicts)
        rep.ok += ok

        gname = _group_of(unit)
        aux = _is_auxiliary(gname)
        if not aux:
            rep.main_verdicts += len(verdicts)
            rep.main_ok += ok
        if gname:
            row = groups[key].get(gname)
            if row is None:
                row = groups[key][gname] = GroupRow(name=gname, auxiliary=aux)
            row.verdicts += len(verdicts)
            row.ok += ok

    for key, rep in reports.items():
        rep.groups = list(groups.get(key, {}).values())
    return sorted(reports.values(),
                  key=lambda r: (-r.verdicts, r.file, r.object_class))


def polarization(files=None, by: str = "drawing") -> dict:
    """图纸级两极分化：每类里全对图 / 全错图 / 混合图的张数。

    ``by="drawing"`` 用 `source.drawing_id`（缺失时退到 `tile`、再退到单元），
    ``by="unit"`` 用单元编号 —— 后者是 `docs/GOLD_STANDARD_REVIEW.md` 的旧口径。

    对照组与被闸删掉组的单元**不参与**：它们的红线是特意画在空白处的，
    整组「全对」会把两极占比推高，而那不是图纸的性质。
    """
    files = load_gold_files() if files is None else files
    cells: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    keyed_by_drawing: dict = collections.defaultdict(lambda: True)

    for fname, unit, cls_name, cls in _iter_classes(files):
        verdicts = cls.get("verdicts") or []
        if not verdicts or _is_auxiliary(_group_of(unit)):
            continue
        src = unit.get("source") or {}
        if by != "unit" and not (src.get("drawing_id") or src.get("tile")):
            keyed_by_drawing[cls_name] = False
        key = _drawing_key(unit, by)
        cells[cls_name][key].extend(bool(v.get("ok")) for v in verdicts)

    out: dict = {}
    for cls_name, drawings in cells.items():
        row = PolarizationRow(
            object_class=cls_name,
            resolution=("unit" if by == "unit"
                        else "drawing" if keyed_by_drawing[cls_name] else "unit(退化)"))
        for flags in drawings.values():
            if len(flags) < MIN_CELLS_PER_DRAWING:
                continue
            row.drawings += 1
            if all(flags):
                row.all_ok += 1
            elif not any(flags):
                row.all_bad += 1
            else:
                row.mixed += 1
        if row.drawings:
            out[cls_name] = row
    return out


def taxonomy_summary(files=None) -> TaxonomySummary:
    """全部 `ok=False` 裁决的 `what` 归一汇总（含来源批次）。"""
    files = load_gold_files() if files is None else files
    pairs = []
    for fname, _unit, _cls_name, cls in _iter_classes(files):
        for v in (cls.get("verdicts") or []):
            if not v.get("ok"):
                pairs.append((fname, v.get("what")))
    return summarize(pairs)


def build_report(files=None) -> GoldReport:
    """一次算齐全部常设指标。"""
    files = load_gold_files() if files is None else files
    classes = class_reports(files)
    rep = GoldReport(
        total_files=len(files),
        total_units=sum(len(f["data"].get("units") or []) for f in files),
        total_verdicts=sum(c.verdicts for c in classes),
        total_ok=sum(c.ok for c in classes),
        classes=classes,
        polarization_by_drawing=polarization(files),
        polarization_by_unit=polarization(files, by="unit"),
        taxonomy=taxonomy_summary(files),
        raw_sheets=dict(RAW_SHEETS),
    )
    return rep


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def render_markdown(rep: GoldReport, issues=None) -> str:
    """报表的 Markdown 形态。数字一律带分母，便于自己核。"""
    lines: list[str] = []
    add = lines.append

    add("# 金标准常设报表")
    add("")
    add(f"由 `core/model3d/gold/report.py` 生成。共 **{rep.total_files} 个类文件 · "
        f"{rep.total_units} 个单元 · {rep.total_verdicts} 条裁决**"
        f"（其中判对 {rep.total_ok}）。")
    add("")
    add("原始判读答卷不计入（它们的裁决已并入对应金标准文件）："
        + "；".join(f"`{k}` → `{v}`" for k, v in rep.raw_sheets.items()) + "。")
    add("")

    add("## 一、分类汇总")
    add("")
    add("> 主组真阳率**已剔除对照组与被闸删掉组**。混算列留作对照，不要引用。")
    add("")
    add("| 文件 | 类 | 方法 | 单元 | 裁决 | 主组真阳率 | 混算 | 判据回指 |")
    add("|---|---|---|---|---|---|---|---|")
    for c in rep.classes:
        if c.has_rate:
            main = f"{_pct(c.main_rate)}（{c.main_ok}/{c.main_verdicts}）"
            overall = _pct(c.overall_rate)
        else:
            main = overall = "不适用"
        mark = "✅" if c.has_criteria else ("⚠ 未固化" if c.criteria_unfixed else "—")
        add(f"| `{c.file}` | {c.object_class} | {c.method} | {c.units} | "
            f"{c.verdicts} | {main} | {overall} | {mark} |")
    add("")

    grouped = [c for c in rep.classes if c.groups]
    if grouped:
        add("### 分组拆开")
        add("")
        add("| 类 | 分组 | 裁决 | 真阳率 | 性质 |")
        add("|---|---|---|---|---|")
        for c in grouped:
            for g in c.groups:
                kind = "对照/闸删（不并入主口径）" if g.auxiliary else "主组"
                add(f"| {c.object_class} | {g.name} | {g.ok}/{g.verdicts} | "
                    f"{_pct(g.rate)} | {kind} |")
        add("")

    add("## 二、图纸级两极分化")
    add("")
    add(f"> 每张图至少 {MIN_CELLS_PER_DRAWING} 格才计入；对照组与闸删组不计入。")
    add("> 分组口径列为 `unit(退化)` 的类，其单元本就是整个工程或整批语料，"
        "**没有图纸粒度** —— 那几行的两极占比不成立，别当结论读。")
    add("")
    add("| 类 | 分组口径 | 图数 | 全对 | 全错 | 混合 | 两极占比 |")
    add("|---|---|---|---|---|---|---|")
    for name, row in sorted(rep.polarization_by_drawing.items(),
                            key=lambda kv: -(kv[1].share or 0)):
        add(f"| {name} | {row.resolution} | {row.drawings} | {row.all_ok} | "
            f"{row.all_bad} | {row.mixed} | {_pct(row.share)} |")
    add("")
    differing = [(k, v) for k, v in rep.polarization_by_unit.items()
                 if k not in rep.polarization_by_drawing
                 or rep.polarization_by_drawing[k].drawings != v.drawings]
    if differing:
        add("按**单元**分组会得到不同的数（单元比图纸细，一张图切成多个瓦片时）。"
            "以下是两个口径分歧的类，`docs/GOLD_STANDARD_REVIEW.md` 用的是单元口径：")
        add("")
        add("| 类 | 单元口径图数 | 单元口径两极 | 图纸口径图数 | 图纸口径两极 |")
        add("|---|---|---|---|---|")
        for name, row in sorted(differing, key=lambda kv: -(kv[1].share or 0)):
            d = rep.polarization_by_drawing.get(name)
            add(f"| {name} | {row.drawings} | {_pct(row.share)} | "
                f"{d.drawings if d else '—'} | {_pct(d.share) if d else '—'} |")
        add("")

    tax = rep.taxonomy
    add("## 三、误检归一")
    add("")
    add(f"共 **{tax.total} 条**误检。归一表见 `core/model3d/gold/taxonomy.py`。")
    add("")
    add("| 桶 | 含义 | 条数 | 占比 |")
    add("|---|---|---|---|")
    for bucket, count in tax.buckets.items():
        add(f"| `{bucket}` | {BUCKETS.get(bucket, '')} | {count} | "
            f"{_pct(count / tax.total if tax.total else None)} |")
    add("")
    add("| 归一标签 | 条数 | 原始写法（来源批次）|")
    add("|---|---|---|")
    for label, count in tax.labels.items():
        variants = "；".join(
            f"`{raw}`×{n}（{'、'.join(sorted(tax.sources.get(raw, ())))}）"
            for raw, n in tax.variants.get(label, {}).items())
        add(f"| {label} | {count} | {variants} |")
    add("")
    if tax.unmapped:
        add("**未登记的写法**（归一表落后于数据，需补）：")
        add("")
        for raw, n in tax.unmapped.items():
            add(f"- `{raw}` × {n}")
        add("")

    add("## 四、判据回指覆盖率")
    add("")
    add(f"{rep.classes_with_criteria}/{len(rep.classes)} = "
        f"**{_pct(rep.criteria_coverage)}** 的类回指了 `CRITERIA.md` 的真实小节。")
    add("")
    unfixed = [c for c in rep.classes if c.criteria_unfixed]
    if unfixed:
        add("以下批次**判据未固化**，与后续批次不可比（已在数据里如实标注）：")
        add("")
        for c in unfixed:
            add(f"- `{c.file}` / {c.object_class}：{c.criteria[0]}")
        add("")

    issues = check_gold_dir() if issues is None else issues
    add("## 五、结构校验")
    add("")
    errors = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] != "error"]
    add(f"error {len(errors)} 条 · warn {len(warns)} 条。")
    add("")
    for i in errors + warns:
        add(f"- [{i['level']}] `{i['file']}` {i['code']}：{i['message']}")
    add("")
    return "\n".join(lines)
