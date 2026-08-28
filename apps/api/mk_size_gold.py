import json, collections
res = {r["id"]: r for r in json.load(open("/tmp/gpt_size/verdicts.json"))["results"]}
man = {}
for l in open("/tmp/gpt_size/manifest.tsv").read().splitlines()[1:]:
    tag, proj, did, ti, w, h = l.split("\t")
    man[tag] = {"proj": proj, "did": did, "title": ti, "w": w, "h": h}
NOTE = (
    "柱轮廓吻合度：先判「那里有没有柱」，再判「红框与柱的真实轮廓吻不吻合」。"
    "**特意不让读尺寸数字**——字符转写是已知不可靠的能力边界，改为纯视觉比例判断。"
    "样本取自**当前代码**逐张 recognize() 的输出，不是存量场景"
    "（实测存量比当前代码多 46%/82% 的柱）。"
    "结果 13/60 = 22%，与 `verdicts_v1` 的 0.59 **冲突**：两批在抽样源与上下文倍数上"
    "的差异都指向本批应当更好，剩下的唯一解释是**问法**——本批给出 7 种具名的"
    "「不是」理由，说不的门槛更低。**未据此覆盖 0.59**，两个数并存待对照实验分辨。"
    "另一条负面结果：识别宽度不能区分真假（真柱中位 423 mm，误检 394 mm），"
    "**没有基于尺寸的修法**。判为真的 13 根里 11 根轮廓吻合。")
by = collections.defaultdict(list)
for tag, m in man.items():
    r = res[tag]
    good = r["there"] == "yes"
    note = f"{m['w']}×{m['h']} mm"
    if good and r["fit"] != "ok":
        note += f"；轮廓{'偏大' if r['fit']=='too_big' else '只盖住一部分'}"
    by[m["proj"]].append({"ref": tag, "ok": good,
                          **({} if good else {"what": r["there"]}), "note": note})
units = [{"unit": f"CO-{proj}", "source": {"project": proj},
          "classes": {"column_outline": {"method": "verdicts", "verdicts": v,
                                         "confidence": 1.0, "verified_by": ["gpt"],
                                         "note": NOTE}}}
         for proj, v in sorted(by.items())]
tot = sum(len(u["classes"]["column_outline"]["verdicts"]) for u in units)
ok = sum(1 for u in units for x in u["classes"]["column_outline"]["verdicts"] if x["ok"])
json.dump({"version": 1, "object_classes": ["column_outline"], "units": units},
          open("/app/data/model3d/gold/column_outline_v1.json", "w"),
          ensure_ascii=False, indent=1)
from core.model3d.gold.audit import audit_units
print(f"column_outline_v1.json  {len(units)} 单元 / {tot} 裁决 / 真阳 {ok} "
      f"= {ok/tot:.2f} | 自审 {len(audit_units(units))} 条")
