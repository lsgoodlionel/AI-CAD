import json, collections
res = {r["id"]: r for r in json.load(open("/tmp/gpt_unit/verdicts.json"))["results"]}
man = {}
for l in open("/tmp/gpt_unit/manifest.tsv").read().splitlines()[1:]:
    tag, proj, unit, did, ti, disc = l.split("\t")
    man[tag] = {"proj": proj, "unit": unit, "title": ti}
MAP = {"multi_floor": "not_spatial", "no_floor": "not_spatial"}
BAD = {"not_spatial", "partial"}
NOTE = (
    "整图缩略判「这张图画的是工程的哪一部分」。系统给每张图都指派了一个单体，"
    "故 `not_spatial`（无空间范围）与 `partial`（只画一部分）都是无意义的归属。"
    "**50/80 = 62% 的图被指派了单体，却根本没有空间范围或只是局部**。"
    "metro 侧 26 张全判 `main`，而判读里 19 张是 `not_spatial` —— "
    "「全是 main」不是因为只有一个单体，是因为多数图本就不该有单体。"
    "**判读用了上一批（楼层批）的分类词 10 格**（multi_floor 9 · no_floor 1），"
    "不在本批清单里，按语义归入 not_spatial 并在此如实记录 —— 跨批次污染的证据。"
    "现成的 `NON_FLOOR_ROLES` 闸（上一轮为楼层归属做的）**能删 31/50 张错的、"
    "只误伤 1/30 张对的**；未在本轮接线，因 `detect_building_unit` 所在文件"
    "正被另一任务修改。")
by = collections.defaultdict(list)
for tag, m in man.items():
    r = res[tag]
    sc = MAP.get(r["scope"], r["scope"])
    good = sc not in BAD
    note = f"系统指派 unit={m['unit']}；判读 {sc}"
    if r["scope"] in MAP:
        note += f"（判读原词 `{r['scope']}` 来自上一批分类，已归并）"
    by[m["proj"]].append({"ref": tag, "ok": good,
                          **({} if good else {"what": sc}), "note": note})
units = [{"unit": f"BU-{proj}", "source": {"project": proj},
          "classes": {"building_unit": {"method": "verdicts", "verdicts": v,
                                        "confidence": 1.0, "verified_by": ["gpt"],
                                        "note": NOTE}}}
         for proj, v in sorted(by.items())]
tot = sum(len(u["classes"]["building_unit"]["verdicts"]) for u in units)
ok = sum(1 for u in units for x in u["classes"]["building_unit"]["verdicts"] if x["ok"])
json.dump({"version": 1, "object_classes": ["building_unit"], "units": units},
          open("/app/data/model3d/gold/building_unit_v1.json", "w"),
          ensure_ascii=False, indent=1)
from core.model3d.gold.audit import audit_units
print(f"building_unit_v1.json  {len(units)} 单元 / {tot} 裁决 / 有意义 {ok} "
      f"= {ok/tot:.2f} | 自审 {len(audit_units(units))} 条")
