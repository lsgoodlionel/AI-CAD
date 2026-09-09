import json, collections
res = {r["id"]: r for r in json.load(open("/tmp/yolo.json"))["results"]}
man = {}
for l in open("/tmp/yolo_man.tsv").read().splitlines()[1:]:
    tag, grp, did, ti = l.split("\t")
    man[tag] = {"grp": grp, "did": did, "title": ti}
NOTE = (
    "规则引擎 × YOLO v5 在**同一批 45 块 640px 瓦片**上的分歧裁决。"
    "实测框数：规则 444 · 模型 205 · 两者一致仅 106 —— "
    "规则框里 76% 模型不认，模型框里 47% 规则不认。"
    "判据从 `CRITERIA.md#columns` 整段照抄，判读者**不知道每格来自哪一方**。"
    "**原始三组**：both 6/20 = 30% · rule_only 1/20 = 5% · model_only 2/20 = 10%，"
    "按事先声明的门槛（对照组须高出分歧组 30%）**有效性检查失败**。"
    "**失败的方式本身是发现**：both 组 14 个错里 **13 个是座椅** —— "
    "28/60 格判为座椅，且只来自 **4 张图**（一张独占 15 格）。"
    "**剔除座椅格后**：both 6/7 = **86%** · rule_only 1/16 = **6%** · "
    "model_only 2/9 = **22%**，检查转为通过。"
    "口径说明：座椅剔除是**事后**按判读结果做的，86% 是「在不是座椅的前提下」"
    "的条件值，不是干净估计；且样本集中（60 格来自 22 张图）。")
by = collections.defaultdict(list)
for t, m in man.items():
    r = res[t]
    note = f"分组={m['grp']}"
    if not r["is_column"]:
        note += f"；实为 {r['what']}"
    by[m["grp"]].append({"ref": t, "ok": r["is_column"],
                         **({} if r["is_column"] else {"what": r["what"]}),
                         "note": note})
units = [{"unit": f"YL-{g}", "source": {"group": g},
          "classes": {"rule_vs_model": {"method": "verdicts", "verdicts": v,
                                        "confidence": 1.0, "verified_by": ["gpt"],
                                        "criteria": "CRITERIA.md#columns",
                                        "note": NOTE}}}
         for g, v in sorted(by.items())]
tot = sum(len(u["classes"]["rule_vs_model"]["verdicts"]) for u in units)
ok = sum(1 for u in units for x in u["classes"]["rule_vs_model"]["verdicts"] if x["ok"])
json.dump({"version": 1, "object_classes": ["rule_vs_model"], "units": units},
          open("/app/data/model3d/gold/rule_vs_model_v1.json", "w"),
          ensure_ascii=False, indent=1)
from core.model3d.gold.audit import audit_units
print(f"rule_vs_model_v1.json  {len(units)} 单元 / {tot} 裁决 / 是柱 {ok} "
      f"= {ok/tot:.2f} | 自审 {len(audit_units(units))} 条")
