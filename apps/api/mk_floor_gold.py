import json, collections, re
res = {r["id"]: r for r in json.load(open("/tmp/gpt_floor/verdicts.json"))["results"]}
man = {}
for l in open("/tmp/gpt_floor/manifest.tsv").read().splitlines()[1:]:
    tag, proj, order, did, ti, disc = l.split("\t")
    man[tag] = {"proj": proj, "order": int(order), "did": did, "title": ti}
NOT_FLOOR = ("说明", "系统图", "原理图", "节点", "详图", "通知单", "大样",
             "目录", "封面", "材料表", "构件表", "门窗表")
SPANS = (re.compile(r"[~～至]"), re.compile(r"上空"), re.compile(r".和.*屋面"))
NOTE = ("整图缩略判「这张图该不该只属于一个楼层」。系统给 80 张每一张都指派了"
        "恰好一层，故任何 multi_floor/no_floor 判读直接是系统错误。"
        "**图名与判读一致认定的确凿错误 12 张 = 15%**；另 7 张缩略图判 detail "
        "而图名写着平面图，未决。哨兵层错 43%，是常规层 20% 的两倍。"
        "问法特意避开读图名——字符转写是已知不可靠的能力边界。")
by = collections.defaultdict(list)
for tag, m in man.items():
    r = res[tag]
    good = r["belongs"] == "single_floor"
    ti = m["title"]
    title_agrees = (any(k in ti for k in NOT_FLOOR) and "平面图" not in ti) \
                   or any(p.search(ti) for p in SPANS)
    note = f"系统指派 order={m['order']}"
    if not good:
        note += f"；判读 {r['belongs']}/{r['content']}"
        note += "；图名一致" if title_agrees else "；**图名写着平面图，未决**"
    by[m["proj"]].append({"ref": tag, "ok": good,
                          **({} if good else {"what": r["belongs"]}), "note": note})
units = [{"unit": f"FL-{proj}",
          "source": {"project": proj},
          "classes": {"floor_assignment": {
              "method": "verdicts", "verdicts": v, "confidence": 1.0,
              "verified_by": ["gpt"], "note": NOTE}}}
         for proj, v in sorted(by.items())]
tot = sum(len(u["classes"]["floor_assignment"]["verdicts"]) for u in units)
ok = sum(1 for u in units for x in u["classes"]["floor_assignment"]["verdicts"] if x["ok"])
json.dump({"version": 1, "object_classes": ["floor_assignment"], "units": units},
          open("/app/data/model3d/gold/floor_assignment_v1.json", "w"),
          ensure_ascii=False, indent=1)
from core.model3d.gold.audit import audit_units
print(f"floor_assignment_v1.json  {len(units)} 单元 / {tot} 裁决 / 正确 {ok} "
      f"= {ok/tot:.2f} | 自审 {len(audit_units(units))} 条")
