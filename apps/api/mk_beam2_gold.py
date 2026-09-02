import json, collections
res = {r["id"]: r for r in json.load(open("/tmp/beam2.json"))["results"]}
man = {}
for l in open("/tmp/beam2_man.tsv").read().splitlines()[1:]:
    tag, grp, did, sc, cf, ti = l.split("\t")
    man[tag] = {"grp": grp, "scale": sc, "conf": cf, "title": ti, "did": did}
NOTE = (
    "梁的复测：判据与 `beams_v1` 逐字相同（固化于 `CRITERIA.md#beams` v7，"
    "含 KL/L/WKL/XL 国标梁代号与「配筋数字不是梁本身」这条），抽样改为**当前代码**。"
    "**有效性检查通过**：12 格空白对照 **0 格**被判成梁。"
    "**结果 21/34 = 62%**，第一版 28/50 = 56%，差 +6%，"
    "**落在判读者 74% 重测信度之内 —— 同一水平**。"
    "误检构成：标注线 4 · 单线 3 · 图框 3 · 空白 2 · 钢筋线 1。"
    "**交叉验证「误检是否跟着比例走」**（本批把每格的 scale/confidence 记进 manifest）："
    "**有**变换记录（置信全是 1.00）的 7/16 = **44%**；**无**变换记录的 14/18 = **78%**；"
    "差 **+34%** —— **带着「满分置信」变换记录的图，梁反而更差**。"
    "其中 `scale=0.01058`（≈1:30）conf=1.00 一组 3/10 = 30%，"
    "正是早先「6 张图 100% 全错」那条发现标记的组合。"
    "**口径**：n 小（16 vs 18，各 6 张图），两组图纸本身可能不同，相关不等于因果；"
    "但方向与比例批**独立**测得的「confidence=1.00 合理率 24%、<1.00 反而 37%」一致。"
    "误检确实按图纸聚集：12 张图里 2 张 ≥2 格且全错。")
by = collections.defaultdict(list)
for t, m in man.items():
    r = res[t]
    if m["grp"] == "blank":
        by["blank_control"].append({
            "ref": t, "ok": r["is_beam"] != "yes",
            **({} if r["is_beam"] != "yes" else {"what": "空白被判成梁"}),
            "note": f"空白对照；判读 {r['is_beam']}/{r['what']}"})
    else:
        good = r["is_beam"] == "yes"
        by["beam"].append({"ref": t, "ok": good,
                           **({} if good else {"what": r["what"]}),
                           "note": f"scale={m['scale']} conf={m['conf']}"})
units = [{"unit": f"B2-{g}", "source": {"group": g},
          "classes": {"beams_retest": {"method": "verdicts", "verdicts": v,
                                       "confidence": 1.0, "verified_by": ["gpt"],
                                       "criteria": "CRITERIA.md#beams",
                                       "note": NOTE}}}
         for g, v in sorted(by.items())]
tot = sum(len(u["classes"]["beams_retest"]["verdicts"]) for u in units)
ok = sum(1 for u in units for x in u["classes"]["beams_retest"]["verdicts"] if x["ok"])
json.dump({"version": 1, "object_classes": ["beams_retest"], "units": units},
          open("/app/data/model3d/gold/beams_retest_v1.json", "w"),
          ensure_ascii=False, indent=1)
from core.model3d.gold.audit import audit_units
print(f"beams_retest_v1.json  {len(units)} 单元 / {tot} 裁决 / 通过 {ok} "
      f"= {ok/tot:.2f} | 自审 {len(audit_units(units))} 条")
