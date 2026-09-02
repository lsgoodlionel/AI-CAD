import json, collections
res = {r["id"]: r for r in json.load(open("/tmp/wall2.json"))["results"]}
man = {}
for l in open("/tmp/wall2_man.tsv").read().splitlines()[1:]:
    tag, grp, did, ti = l.split("\t")
    man[tag] = {"grp": grp, "did": did, "title": ti}
NOTE = (
    "墙的重测：**判据与 `walls_v1` 逐字相同**（已固化于 `CRITERIA.md#walls` v5），"
    "只把抽样源从存量场景换成**当前代码**逐张 `recognize()` —— 单变量对比。"
    "**有效性检查完美通过**：12 格红线画在墨迹稀疏处的对照，"
    "**12/12 全部被判为空白/单线，0 格判成墙**。"
    "结果 21/37 = **57%**，第一版 33/47 = 70%，差 −13%，"
    "**落在判读者 74% 重测信度之内 —— 可视为同一水平**，"
    "不能说变差了，也不能说没变差。"
    "误检构成：楼梯 5 · 标注 4 · 单线 2 · 家具设备 2 · 柱 2 · 空白 1。"
    "**楼梯是最大的单一误检源**（5/16 = 31%）—— 楼梯踏步是平行细线，"
    "而墙的判据正是「两条平行细线之间的条带」，几何上本就相近，"
    "与柱把剧院座椅认成柱网是同一类问题。")
by = collections.defaultdict(list)
for t, m in man.items():
    r = res[t]
    if m["grp"] == "blank":
        by["blank_control"].append({
            "ref": t, "ok": r["is_wall"] != "yes",
            **({} if r["is_wall"] != "yes" else {"what": "空白被判成墙"}),
            "note": f"空白对照；判读 {r['is_wall']}/{r['what']}"})
    else:
        good = r["is_wall"] == "yes"
        by["wall"].append({"ref": t, "ok": good,
                           **({} if good else {"what": r["what"]}),
                           "note": f"判读 {r['is_wall']}"})
units = [{"unit": f"W2-{g}", "source": {"group": g},
          "classes": {"walls_retest": {"method": "verdicts", "verdicts": v,
                                       "confidence": 1.0, "verified_by": ["gpt"],
                                       "criteria": "CRITERIA.md#walls",
                                       "note": NOTE}}}
         for g, v in sorted(by.items())]
tot = sum(len(u["classes"]["walls_retest"]["verdicts"]) for u in units)
ok = sum(1 for u in units for x in u["classes"]["walls_retest"]["verdicts"] if x["ok"])
json.dump({"version": 1, "object_classes": ["walls_retest"], "units": units},
          open("/app/data/model3d/gold/walls_retest_v1.json", "w"),
          ensure_ascii=False, indent=1)
from core.model3d.gold.audit import audit_units
print(f"walls_retest_v1.json  {len(units)} 单元 / {tot} 裁决 / 通过 {ok} "
      f"= {ok/tot:.2f} | 自审 {len(audit_units(units))} 条")
