import json, collections
res = {r["id"]: r for r in json.load(open("/tmp/gpt_slab/verdicts.json"))["results"]}
man = {}
for l in open("/tmp/gpt_slab/manifest.tsv").read().splitlines()[1:]:
    tag, basis, proj, did, ti, area, th = l.split("\t")
    man[tag] = {"basis": basis, "proj": proj, "did": did, "title": ti, "area": area}
SLAB = {"slab_floor", "slab_roof", "slab_raft", "slab_landing"}
by = collections.defaultdict(list)
for tag, m in man.items():
    r = res[tag]
    good = r["what"] in SLAB
    note = r.get("saw", "")
    if good and r.get("extent") in ("too_big", "too_small"):
        note = f"{note}（范围{'圈大了' if r['extent']=='too_big' else '圈小了'}）"
    if r.get("_transcribed_as"):
        note = f"{note}［判读转写成 {r['_transcribed_as']}，按位置纠正］"
    by[m["did"]].append({"ref": tag, "ok": good,
                         **({} if good else {"what": r["what"]}),
                         "note": note if not r.get("confident", True)
                                 else note})
NOTE = ("板候选逐块判读（红色多边形 = 系统认定的板边界）。"
        "误检 32% 是房间轮廓、22% 是墙或梁、18% 是整层外轮廓。"
        "**判为板的 5 块里 4 块「圈大了」——真正圈得对的只有 1/50。**"
        "两个 basis 分层一样差：largest_polygon 3/25、layer 2/25。"
        "另有 6% 的板是 column_envelope/axis_envelope 兜底，"
        "其 src 是合成标记而非图纸 id，**图上无处可看、判读不了**，"
        "而它们扛着 84% 的混凝土量。")
units = [{"unit": f"SL-{did[:8]}",
          "source": {"drawing_id": did[:8],
                     "title": man[[t for t in man if man[t]['did'] == did][0]]["title"]},
          "classes": {"slabs": {"method": "verdicts", "verdicts": v,
                                "confidence": 1.0, "verified_by": ["gpt"],
                                "note": NOTE}}}
         for did, v in sorted(by.items())]
tot = sum(len(u["classes"]["slabs"]["verdicts"]) for u in units)
ok = sum(1 for u in units for x in u["classes"]["slabs"]["verdicts"] if x["ok"])
json.dump({"version": 1, "object_classes": ["slabs"], "units": units},
          open("/app/data/model3d/gold/slabs_v1.json", "w"),
          ensure_ascii=False, indent=1)
from core.model3d.gold.audit import audit_units
print(f"slabs_v1.json  {len(units)} 单元 / {tot} 裁决 / 真阳 {ok} = {ok/tot:.2f} "
      f"| 自审 {len(audit_units(units))} 条")
