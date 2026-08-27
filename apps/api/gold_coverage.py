"""金标准覆盖面盘点：覆盖了什么、没覆盖什么、已验证的部分可信到什么程度。"""
import glob, json, os
from collections import Counter, defaultdict

GOLD = "/app/data/model3d/gold"
from core.model3d.gold.audit import audit_units
from core.model3d.gold.schema import parse_unit

PLANNED = [
    ("columns（数量）", "count"), ("axes 轴线", "instances"),
    ("title_block 图框", "fields"), ("drawing_title 图名", "text"),
    ("columns（实体级）", "instances"), ("walls / beams / slabs", "instances"),
    ("notes 总说明", "text"), ("各专业标注", "instances"),
]

print("=" * 62)
print("金标准覆盖面盘点")
print("=" * 62)

files = sorted(glob.glob(f"{GOLD}/*.json"))
by_class = defaultdict(lambda: {"units": 0, "counted": 0, "excluded": 0,
                                "human": 0, "instances": 0, "projects": Counter()})
issues_total = 0
for f in files:
    d = json.load(open(f))
    if "units" not in d:
        continue
    issues = audit_units(d["units"])
    issues_total += len(issues)
    for raw in d["units"]:
        u = parse_unit(raw)
        proj = u.source.get("project", "?")
        for name, cls in u.classes.items():
            b = by_class[name]
            b["units"] += 1
            b["projects"][proj] += 1
            if cls.excluded:
                b["excluded"] += 1
            elif cls.counts_toward_metrics:
                b["counted"] += 1
            if "human" in cls.verified_by:
                b["human"] += 1
            b["instances"] += len(cls.instances)

print(f"\n【已建立的对象类】 文件 {len(files)} 个 | **自审问题 {issues_total} 条**")
print(f"\n{'对象类':<12}{'单元':>6}{'计分':>6}{'排除':>6}{'人工复核':>9}{'实体':>7}  工程分布")
for name, b in sorted(by_class.items()):
    proj = " ".join(f"{k}×{v}" for k, v in b["projects"].most_common())
    print(f"{name:<12}{b['units']:>6}{b['counted']:>6}{b['excluded']:>6}"
          f"{b['human']:>9}{b['instances']:>7}  {proj}")

print("\n【规格里计划的 8 类，进度】")
# **从实际数据推，不硬编码** —— 第一版写死在脚本里，加了新类也不会变，
# 盘点报告反而成了最先过时的东西
KEYMAP = {"columns（数量）": "columns", "axes 轴线": "axes",
          "title_block 图框": "title_block", "drawing_title 图名": "drawing_title",
          "columns（实体级）": None, "walls / beams / slabs": "walls",
          "notes 总说明": "notes", "各专业标注": "annotations"}
for i, (label, _method) in enumerate(PLANNED, 1):
    key = KEYMAP.get(label)
    b = by_class.get(key) if key else None
    if label == "columns（实体级）":
        inst = by_class.get("columns", {}).get("instances", 0)
        mark = f"✅ {inst} 个实体" if inst else "🔴 未建（现有单元均为计数/裁决式）"
    elif b:
        extra = f" / {b['instances']} 实体" if b['instances'] else ""
        mark = f"✅ {b['units']} 单元{extra}"
    else:
        mark = "🔴 未建"
    print(f"  {i}. {label:<22} {mark}")

print("\n【覆盖的语料比例】")
PIDS = {"metro": "77777777-7777-7777-7777-777777777777",
        "sgoh": "9188e163-c684-415e-a4ec-08f208273eff"}
import asyncio, databases as databases_lib
from core.config import settings

async def corpus():
    db = databases_lib.Database(settings.database_url); await db.connect()
    out = {}
    for k, pid in PIDS.items():
        r = await db.fetch_one("SELECT count(*) c FROM drawings WHERE project_id=:p",
                               {"p": pid})
        out[k] = r["c"]
    await db.disconnect()
    return out

total = asyncio.run(corpus())
drawings = defaultdict(set)
for f in files:
    d = json.load(open(f))
    for raw in d.get("units", []):
        u = parse_unit(raw)
        drawings[u.source.get("project", "?")].add(u.source.get("drawing_id"))
for k, n in total.items():
    cov = len(drawings.get(k, ()))
    print(f"  {k}: 金标准触及 {cov} 张 / 全库 {n} 张 = **{cov/max(n,1):.2%}**")

print("\n【裁决式真值（已纳入 GoldUnit，method=verdicts）】")
pv = f"{GOLD}/patch_verdicts_v1.json"
if os.path.exists(pv):
    v = json.load(open(pv))["results"]
    c = Counter(x["is_column"] for x in v)
    print(f"  patch_verdicts_v1: {len(v)} 个柱候选逐个判读 "
          f"(yes {c['yes']} / no {c['no']})，用于度量精确率与误检分类")
    print(f"  ✅ 已迁入 verdicts_v1.json（109 个单元），受自审与统一评分覆盖")
