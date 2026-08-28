import json, collections
FIX2 = {"JUGN": "JU9N", "YMMF": "YMFF"}
FIX1 = {"7NZM": "7N2M", "WYTF": "WY7F"}
v2 = {FIX2.get(r["id"], r["id"]): r for r in
      json.load(open("/tmp/gpt_disc2/verdicts.json"))["results"]}
v1 = {FIX1.get(r["id"], r["id"]): r for r in
      json.load(open("/tmp/gpt_disc/verdicts.json"))["results"]}
m2, m1 = {}, {}
for l in open("/tmp/gpt_disc2/manifest.tsv").read().splitlines()[1:]:
    t, p, s, d, ti = l.split("\t"); m2[t] = {"proj": p, "did": d, "sys": s, "title": ti}
for l in open("/tmp/gpt_disc/manifest.tsv").read().splitlines()[1:]:
    t, p, s, d, ti, db = l.split("\t"); m1[t] = {"did": d}
a1 = {m1[t]["did"]: v1[t]["disc"] for t in v1 if t in m1}
NOTE = (
    "**只记「这张图有没有专业」，不记「是哪个专业」。** 专业本身两次实测都不够格："
    "整图缩略 21%、局部高倍 27%，而同一批图两次独立判读的**重测信度只有 74%** —— "
    "判读者自己都不稳定到能给专业定标。"
    "但 `not_drawing` 这一档是稳的：两次独立渲染的交集 19/23，"
    "且 **22/23 = 96% 能被图名证实**（目录/说明/通知单/清单/表/封面/图例/材料）。"
    "系统给每一张图都指派了一个专业，而其中约四分之一根本没有专业可言。"
    "受控对比另有一条：同样 90 张图、同样问题与判据，只把 DPI 44 整图缩略换成 "
    "DPI 130 局部裁剪，**机电召回从 1/20 = 5% 升到 5/20 = 25%** —— "
    "渲染分辨率要匹配问题所在的尺度。")
by = collections.defaultdict(list)
for t, m in m2.items():
    r2 = v2[t]["disc"]
    r1 = a1.get(m["did"])
    nd = r2 == "not_drawing"
    note = f"系统指派 {m['sys']}；第二版判 {r2}"
    if r1 is not None:
        note += f"；第一版判 {r1}" + ("（两版一致）" if r1 == r2 else "")
    by[m["proj"]].append({"ref": t, "ok": not nd,
                          **({} if not nd else {"what": "no_discipline"}),
                          "note": note})
units = [{"unit": f"ND-{proj}", "source": {"project": proj},
          "classes": {"has_discipline": {"method": "verdicts", "verdicts": v,
                                         "confidence": 1.0, "verified_by": ["gpt"],
                                         "criteria": "CRITERIA.md#discipline",
                                         "note": NOTE}}}
         for proj, v in sorted(by.items())]
tot = sum(len(u["classes"]["has_discipline"]["verdicts"]) for u in units)
ok = sum(1 for u in units for x in u["classes"]["has_discipline"]["verdicts"] if x["ok"])
json.dump({"version": 1, "object_classes": ["has_discipline"], "units": units},
          open("/app/data/model3d/gold/has_discipline_v1.json", "w"),
          ensure_ascii=False, indent=1)
from core.model3d.gold.audit import audit_units
print(f"has_discipline_v1.json  {len(units)} 单元 / {tot} 裁决 / 有专业 {ok} "
      f"= {ok/tot:.2f}（{tot-ok} 张没有专业）| 自审 {len(audit_units(units))} 条")
