import json, collections
res = {r["id"]: r for r in json.load(open("/tmp/gpt_mep/verdicts.json"))["results"]}
man = {}
for l in open("/tmp/gpt_mep/manifest.tsv").read().splitlines()[1:]:
    k, tag, did, ti, di = l.split("\t")
    man[tag] = {"kind": k, "did": did, "title": ti}

for kind, prefix, cls, okf, whatf, note in (
    ("equipment", "E", "equipment", "is_equipment", "kind",
     "设备候选逐个判读。误检 42/50 是柱墙 —— 柱和墙的尺寸正落在设备尺寸带里，"
     "光靠尺寸分不开；rect 分支实测产出 0 个，设备全部来自 poly 分支。"),
    ("pipes", "P", "pipes", "is_pipe", "system",
     "管线候选逐个判读，**无一是管线**：墙 30 / 结构线 21 / 标注线 7。"
     "当时 `_find_pipes` 唯一判据是「够长且不是轴线」，于是每条墙线梁线尺寸线都成了管。"),
):
    by = collections.defaultdict(list)
    for tag, m in man.items():
        if m["kind"] != kind:
            continue
        r = res[tag]
        by[m["did"]].append({"ref": tag, "ok": bool(r[okf]),
                             **({} if r[okf] else {"what": r[whatf]}),
                             "note": "" if r["confident"] else "判读者标注看不清"})
    units = [{
        "unit": f"{prefix}-{did[:8]}",
        "source": {"drawing_id": did[:8], "title": man[[t for t in man if man[t]['did'] == did][0]]["title"]},
        "classes": {cls: {"method": "verdicts", "verdicts": v, "confidence": 1.0,
                          "verified_by": ["gpt"], "note": note}},
    } for did, v in sorted(by.items())]
    tot = sum(len(u["classes"][cls]["verdicts"]) for u in units)
    ok = sum(1 for u in units for x in u["classes"][cls]["verdicts"] if x["ok"])
    json.dump({"version": 1, "object_classes": [cls], "units": units},
              open(f"/app/data/model3d/gold/{cls}_v1.json", "w"),
              ensure_ascii=False, indent=1)
    from core.model3d.gold.audit import audit_units
    print(f"{cls}_v1.json  {len(units)} 单元 / {tot} 裁决 / 真阳 {ok} "
          f"= 精确率 {ok/tot:.2f} | 自审 {len(audit_units(units))} 条")
