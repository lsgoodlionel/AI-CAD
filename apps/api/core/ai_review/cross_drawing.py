"""套图级跨图分析（纯 SQL + Python 聚合，无 LLM 调用）。

analyze_batch 对一个审图批次内的图纸做整体性检查：
- 重复图号 / 版本冲突（同图号多版本同时在审）
- 接口缺图（issue.interface_related 指向的粗专业在套图内无对应图纸）
- 问题聚类（按 location_json 的 楼层+轴线 归一化 key，聚合 ≥2 张图共有问题）
- 高频对象聚合（review_method.优先对象 汇总）
- 严重度分布 / 专业分布

蓝图：docs/BATCH_REVIEW_BLUEPRINT.md 第 4.6 节。
"""
import json
from collections import Counter, defaultdict
from typing import Any

SEVERITY_KEYS = ("critical", "major", "minor", "info")

# 19 专业中文名 → 粗专业映射（与 services/reviewAudit 专业口径一致）
INTERFACE_DISCIPLINE_MAP = {
    "结构": "structure", "围护": "structure", "桩基": "structure",
    "人防": "structure", "钢结构": "structure", "基坑": "structure",
    "建筑": "architecture", "节能": "architecture", "幕墙": "architecture",
    "景观": "architecture", "室外总体": "architecture",
    "机电综合": "mep", "给排水": "mep", "电气": "mep",
    "暖通": "mep", "弱电": "mep", "消防": "mep",
    "装饰装修": "decoration",
    "综合协调": "general",
}

_DRAWINGS_SQL = """
SELECT id, drawing_no, version, discipline
FROM drawings
WHERE id::text = ANY(:drawing_ids)
"""

_ISSUES_SQL = """
SELECT r.drawing_id, d.drawing_no, d.discipline, i.severity,
       i.location_json, i.interface_related, i.review_method
FROM ai_review_issues i
JOIN ai_review_reports r ON r.id = i.report_id
JOIN drawings d ON d.id = r.drawing_id
WHERE r.drawing_id::text = ANY(:drawing_ids)
"""


def _safe_json(value: Any, default: Any) -> Any:
    """JSONB 字段经驱动可能返回 str，安全解析；类型不符时返回默认值。"""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return default
    if not isinstance(value, type(default)):
        return default
    return value


def _empty_result() -> dict:
    """空批次的零值结构。"""
    return {
        "重复图号": [],
        "版本冲突": [],
        "接口缺图": [],
        "问题聚类": [],
        "高频对象聚合": [],
        "严重度分布": {key: 0 for key in SEVERITY_KEYS},
        "专业分布": {},
    }


def _find_duplicates(drawings: list) -> tuple[list, list]:
    """重复图号与版本冲突检测。"""
    by_no: dict[str, list] = defaultdict(list)
    for row in drawings:
        by_no[row["drawing_no"]].append(row)

    duplicates, conflicts = [], []
    for drawing_no, rows in by_no.items():
        if len(rows) < 2:
            continue
        duplicates.append({
            "drawing_no": drawing_no,
            "drawing_ids": [str(row["id"]) for row in rows],
        })
        versions = sorted({row["version"] for row in rows if row["version"]})
        if len(versions) > 1:
            conflicts.append({"drawing_no": drawing_no, "versions": versions})
    return duplicates, conflicts


def _find_missing_interfaces(issues: list, present_disciplines: set[str]) -> list:
    """接口缺图：issue 指向的粗专业在套图内无对应图纸。"""
    missing: dict[str, list] = defaultdict(list)
    for issue in issues:
        interfaces = _safe_json(issue["interface_related"], [])
        for name in interfaces:
            coarse = INTERFACE_DISCIPLINE_MAP.get(str(name))
            if coarse is None or coarse in present_disciplines:
                continue
            missing[coarse].append(
                {"drawing_no": issue["drawing_no"], "interface": str(name)}
            )
    return [
        {"missing_discipline": discipline, "referenced_by": refs}
        for discipline, refs in missing.items()
    ]


def _location_key(location: dict) -> str | None:
    """楼层+轴线 归一化 key；两者皆空则不可聚类。"""
    levels = sorted(str(x) for x in location.get("levels") or [])
    axes = sorted(str(x) for x in location.get("axes") or [])
    if not levels and not axes:
        return None
    return "|".join(levels) + "@" + "|".join(axes)


def _cluster_issues(issues: list) -> list:
    """按定位 key 聚合 ≥2 张图共有的问题。"""
    clusters: dict[str, dict] = {}
    for issue in issues:
        location = _safe_json(issue["location_json"], {})
        key = _location_key(location)
        if key is None:
            continue
        cluster = clusters.setdefault(
            key, {"location_key": key, "count": 0, "drawings": set(), "disciplines": set()}
        )
        cluster["count"] += 1
        cluster["drawings"].add(issue["drawing_no"])
        cluster["disciplines"].add(issue["discipline"])
    return [
        {
            "location_key": c["location_key"],
            "count": c["count"],
            "drawings": sorted(c["drawings"]),
            "disciplines": sorted(c["disciplines"]),
        }
        for c in clusters.values()
        if len(c["drawings"]) >= 2
    ]


def _aggregate_priority_objects(issues: list) -> list:
    """高频对象聚合：review_method.优先对象 按 name 计数，降序输出。"""
    counter: Counter[str] = Counter()
    for issue in issues:
        method = _safe_json(issue["review_method"], {})
        for obj in method.get("优先对象") or []:
            if isinstance(obj, dict) and obj.get("name"):
                counter[str(obj["name"])] += 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": name, "count": count} for name, count in ranked]


def _distributions(issues: list) -> tuple[dict, dict]:
    """严重度分布（固定四档）与专业分布。"""
    severity = {key: 0 for key in SEVERITY_KEYS}
    discipline: Counter[str] = Counter()
    for issue in issues:
        if issue["severity"] in severity:
            severity[issue["severity"]] += 1
        discipline[issue["discipline"]] += 1
    return severity, dict(discipline)


async def analyze_batch(db, project_id: str, drawing_ids: list[str]) -> dict:
    """跨图分析入口：两次查询（图纸行 + 问题行）后纯 Python 聚合。"""
    if not drawing_ids:
        return _empty_result()

    ids = [str(did) for did in drawing_ids]
    drawings = await db.fetch_all(_DRAWINGS_SQL, {"drawing_ids": ids})
    issues = await db.fetch_all(_ISSUES_SQL, {"drawing_ids": ids})

    duplicates, conflicts = _find_duplicates(list(drawings))
    present = {row["discipline"] for row in drawings}
    severity_dist, discipline_dist = _distributions(list(issues))

    return {
        "重复图号": duplicates,
        "版本冲突": conflicts,
        "接口缺图": _find_missing_interfaces(list(issues), present),
        "问题聚类": _cluster_issues(list(issues)),
        "高频对象聚合": _aggregate_priority_objects(list(issues)),
        "严重度分布": severity_dist,
        "专业分布": discipline_dist,
    }
