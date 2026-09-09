"""金标准文件的**结构**校验 —— 每个文件长得一不一样。

与 `audit.py` 分工：

* `audit.py` 查**真值内容**对不对（轴号违反国标、轴距链不闭合、把握恰在阈值）；
* 本模块查**文件结构**齐不齐（缺 `units`、判据不回指、表头与单元对不上）。

两者都跑，本模块把 `audit.py` 的结果一并收进同一份清单，
调用方不必记得还有第二套要跑 —— 忘了跑的检查等于没有。

**原始判读接触表的豁免**：`patch_verdicts_v1.json` 不是金标准单元文件，
是判读者交回的原始答卷，它的 120 行**已逐条并入** `verdicts_v1.json`
（refs 完全相同、`ok` 零处不一致）。把它当成金标准再数一遍，
总数会从 1096 虚增到 1216。所以它**声明** `kind: raw_judgement_sheet`
并指明 `materialized_in`，校验按这个声明放行 ——
豁免写在数据里，而不是写死在校验脚本的文件名清单里。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .audit import audit_units
from .schema import METHODS, parse_unit

#: 金标准数据目录（`core/model3d/gold/` → parents[3] 为 apps/api 根）。
_GOLD_DIR = Path(__file__).parents[3] / "data" / "model3d" / "gold"

#: 判据文件。判据回指的锚点从它的小节标题里取。
CRITERIA_FILE = "CRITERIA.md"

#: 已知的原始答卷（非金标准单元文件）。仅用于报表说明，
#: **放行与否以文件自带的 `kind` 为准**。
RAW_SHEETS = {"patch_verdicts_v1.json": "verdicts_v1.json"}

#: 文件自称原始答卷时用的 `kind` 值。
RAW_KIND = "raw_judgement_sheet"

#: 判据字段以此开头，表示「本批判据未固化」——
#: 允许如实标注，但这类批次与后续批次**不可比**。
UNFIXED_PREFIX = "UNFIXED"


def gold_dir() -> Path:
    """金标准数据目录。"""
    return _GOLD_DIR


def _issue(code: str, level: str, file: str, message: str, **extra) -> dict:
    return {"code": code, "level": level, "file": file,
            "message": message, **extra}


def criteria_sections(directory: Path | None = None) -> set[str]:
    """从 `CRITERIA.md` 的小节标题里取出可回指的锚点。

    标题形如 `## columns / column_outline —— 什么算「柱」`，
    一个标题可以同时是几个类的锚点（`/` 分隔）。
    """
    path = (directory or _GOLD_DIR) / CRITERIA_FILE
    if not path.is_file():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("## "):
            continue
        head = re.split(r"——|--", line[3:])[0]
        for part in head.split("/"):
            token = part.strip().strip("`").strip()
            if token:
                out.add(token)
    return out


def _anchor(criteria: str) -> str | None:
    """`CRITERIA.md#columns（本批未含…）` → `columns`；无锚点返回 None。"""
    if "#" not in criteria:
        return None
    tail = criteria.split("#", 1)[1]
    return re.split(r"[（(\s]", tail)[0].strip() or None


def check_file(name: str, data, *,
               criteria_sections: set[str] | None = None) -> list[dict]:
    """校验一个金标准文件的结构，返回问题清单（空表示无问题）。

    ``criteria_sections`` 给出可回指的锚点集合；不给就不查悬空回指
    （拿不到判据文件时不该把「查不了」报成「查出问题」）。
    """
    anchors = criteria_sections
    out: list[dict] = []
    if not isinstance(data, dict):
        return [_issue("non_standard_shape", "error", name,
                       f"顶层不是对象而是 {type(data).__name__}")]

    if data.get("kind") == RAW_KIND:
        # 原始答卷：只要求它说清自己被哪个金标准文件收编了
        if not data.get("materialized_in"):
            out.append(_issue(
                "raw_sheet_unlinked", "error", name,
                f"声明为 {RAW_KIND} 却没写 materialized_in —— "
                "读者无从判断这些裁决是否已被别处收录"))
        return out

    units = data.get("units")
    if not isinstance(units, list):
        return [_issue("non_standard_shape", "error", name,
                       "缺少 units 列表 —— 汇总必须为它特判，"
                       f"若这是原始答卷请声明 kind={RAW_KIND!r}")]
    if "version" not in data:
        out.append(_issue("missing_version", "warn", name, "缺少 version"))

    declared = set(data.get("object_classes") or ())
    seen_classes: set[str] = set()
    #: 类名 → {判据原文 或 None}。判据是**整批**的属性，逐单元报会刷屏
    #: （实测 verdicts_v1 一个文件就能刷出 109 条同样的话）。
    criteria_seen: dict[str, set] = {}
    verdict_classes: set[str] = set()

    for raw in units:
        # 编号重复由 audit_units 统一报，这里不重复报一遍
        uid = str((raw or {}).get("unit", "?"))
        try:
            unit = parse_unit(raw)
        except (ValueError, KeyError, TypeError) as exc:
            out.append(_issue("unparsable", "error", name, f"{uid}: {exc}"))
            continue

        for cls_name, cls in unit.classes.items():
            seen_classes.add(cls_name)
            if cls.method not in METHODS:
                out.append(_issue("bad_method", "error", name,
                                  f"{uid}/{cls_name}: 未知 method {cls.method!r}"))
            if cls.method == "verdicts" and not cls.excluded:
                verdict_classes.add(cls_name)
            criteria_seen.setdefault(cls_name, set()).add(
                (cls.criteria or "").strip() or None)

    for cls_name, variants in criteria_seen.items():
        out.extend(_check_criteria(name, cls_name, variants,
                                   cls_name in verdict_classes, anchors))

    missing = seen_classes - declared
    extra = declared - seen_classes
    if missing or extra:
        out.append(_issue(
            "object_classes_mismatch", "error", name,
            f"表头 object_classes={sorted(declared)} 与单元里实际出现的 "
            f"{sorted(seen_classes)} 对不上"))

    out.extend(_issue(i["code"], "error", name, f"{i['unit']}: {i['message']}")
               for i in audit_units(units))
    return out


def _check_criteria(name, cls_name, variants: set, is_verdict_class: bool,
                    anchors) -> list[dict]:
    """判据回指：不回指就不可比 —— 这是 0.59 vs 0.22 的病根。

    ``variants`` 是该类在**全部单元**里出现过的判据原文集合。
    多于一个说明同一批用了两套判据，那正是不可比的成因，必须报出来。
    """
    out: list[dict] = []
    if len(variants) > 1:
        out.append(_issue("criteria_inconsistent", "error", name,
                          f"{cls_name}: 同一类的单元用了 {len(variants)} 套判据 "
                          f"{sorted(str(v) for v in variants)} —— 组间不可比"))
    for criteria in variants:
        if not criteria:
            # 只对**裁决式**批次强制：count/text/fields/instances 记的是
            # 计数与字段，判据是「照着图数」，没有松紧可言。
            if is_verdict_class:
                out.append(_issue(
                    "criteria_missing", "error", name,
                    f"{cls_name}: 未回指判据 —— 判据不固定，数字就不可比；"
                    f"补不出就标 {UNFIXED_PREFIX}: 说明理由"))
            continue
        if criteria.upper().startswith(UNFIXED_PREFIX):
            continue
        anchor = _anchor(criteria)
        if anchor is None:
            out.append(_issue("criteria_dangling", "warn", name,
                              f"{cls_name}: 判据 {criteria!r} 没有 # 锚点"))
        elif anchors is not None and anchor not in anchors:
            out.append(_issue("criteria_dangling", "error", name,
                              f"{cls_name}: 判据锚点 #{anchor} 在 "
                              f"{CRITERIA_FILE} 里不存在"))
    return out


def _json_files(directory: Path) -> list[Path]:
    """目录下的金标准 JSON。

    **跳过点开头的文件**：把仓库打包送进容器时，macOS 的扩展属性会变成
    `._xxx.json` 这类 AppleDouble 伴生文件，它们不是 UTF-8，会让整个加载
    在读第一个文件时就崩掉。金标准文件不会以点开头，跳过是安全的。
    """
    return [p for p in directory.glob("*.json") if not p.name.startswith(".")]


def load_gold_files(directory: Path | None = None) -> list[dict]:
    """读入目录下**所有金标准单元文件**（原始答卷不算）。

    返回 `[{"name": 文件名, "data": 原始 dict}]`，按文件名排序。
    """
    d = directory or _GOLD_DIR
    if not d.is_dir():
        return []
    out = []
    for path in sorted(_json_files(d)):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("kind") == RAW_KIND:
            continue
        out.append({"name": path.name, "data": data})
    return out


def check_gold_dir(directory: Path | None = None) -> list[dict]:
    """校验整个目录，含跨文件的重名检查。"""
    d = directory or _GOLD_DIR
    if not d.is_dir():
        return [_issue("missing_dir", "error", str(d), "金标准目录不存在")]

    anchors = criteria_sections(d)
    out: list[dict] = []
    unit_owner: dict[str, str] = {}
    class_owner: dict[str, list] = {}

    for path in sorted(_json_files(d)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            out.append(_issue("bad_json", "error", path.name, str(exc)))
            continue
        out.extend(check_file(path.name, data, criteria_sections=anchors))
        if not isinstance(data, dict) or not isinstance(data.get("units"), list):
            continue
        for raw in data["units"]:
            uid = str((raw or {}).get("unit", "?"))
            if uid in unit_owner and unit_owner[uid] != path.name:
                out.append(_issue(
                    "cross_file_unit_clash", "warn", path.name,
                    f"{uid}: 与 {unit_owner[uid]} 的单元同名"))
            unit_owner[uid] = path.name
            for cls_name in (raw.get("classes") or {}):
                class_owner.setdefault(cls_name, []).append(path.name)

    for cls_name, owners in class_owner.items():
        files = sorted(set(owners))
        if len(files) > 1:
            out.append(_issue(
                "class_name_collision", "warn", ",".join(files),
                f"对象类 {cls_name!r} 出现在多个文件里 —— "
                "按类名汇总时会撞在一起，报表按（文件, 类）分行"))
    return out
