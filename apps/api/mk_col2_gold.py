import json, collections
res = {r["id"]: r for r in json.load(open("/tmp/col2.json"))["results"]}
man = {}
for l in open("/tmp/col2_man.tsv").read().splitlines()[1:]:
    tag, grp, did, ti = l.split("\t")
    man[tag] = {"grp": grp, "title": ti}
NOTE = (
    "柱的定案复测 + **密排阵列闸（座椅闸）验证**。判据从 `CRITERIA.md#columns` "
    "整段照抄（含「带叉方块算柱」「柱编号算柱」—— 此前 0.59 vs 0.22 不可比的根源）。"
    "三组混发，判读者不知道每格来源；样本分散在 32 张图、单图最多 4 格。"
    "**有效性检查通过**：空白对照 0/10 被判成柱。"
    "**闸删对了**：被删的 22 格里**误删 0 格**（无一是柱），"
    "其中座椅 6 · 文字 5 · 梁 5 · 尺寸 2 · 空白 2 · 墙 2。"
    "**保留组 3/26 = 12%**（有把握口径 3/21）—— 这是当前代码在完整固定判据下的定案值。"
    "**保留组最大的误检来源是标高符号 7/26 = 27%**；"
    "标注类合计（标高符号 + 尺寸 + 文字 + 填充）**16/26 = 62%**，"
    "构件类误检（梁 + 窗 + 设备）7/26。"
    "口径提醒：此前的 0.59 / 0.22 / 68% / 22% 各自判据与抽样源不同，"
    "**与本值不可比**；本值是首个「固定判据 + 当前代码 + 干净对照 + 分散抽样」下的数字。")
by = collections.defaultdict(list)
for t, m in man.items():
    r = res[t]
    if m["grp"] == "dropped":
        by["dropped_by_gate"].append({
            "ref": t, "ok": not r["is_column"],
            **({} if not r["is_column"] else {"what": "误删：本是柱"}),
            "note": f"被密排阵列闸删掉；实为 {r['what']}"})
    elif m["grp"] == "blank":
        by["blank_control"].append({
            "ref": t, "ok": not r["is_column"],
            **({} if not r["is_column"] else {"what": "空白被判成柱"}),
            "note": f"空白对照；判读 {r['what']}"})
    else:
        by["kept"].append({"ref": t, "ok": r["is_column"],
                           **({} if r["is_column"] else {"what": r["what"]}),
                           "note": ("判读有把握" if r["confident"] else "判读没把握")})
units = [{"unit": f"C2-{g}", "source": {"group": g},
          "classes": {"columns_final": {"method": "verdicts", "verdicts": v,
                                        "confidence": 1.0, "verified_by": ["gpt"],
                                        "criteria": "CRITERIA.md#columns",
                                        "note": NOTE}}}
         for g, v in sorted(by.items())]
tot = sum(len(u["classes"]["columns_final"]["verdicts"]) for u in units)
ok = sum(1 for u in units for x in u["classes"]["columns_final"]["verdicts"] if x["ok"])
json.dump({"version": 1, "object_classes": ["columns_final"], "units": units},
          open("/app/data/model3d/gold/columns_final_v1.json", "w"),
          ensure_ascii=False, indent=1)
from core.model3d.gold.audit import audit_units
print(f"columns_final_v1.json  {len(units)} 单元 / {tot} 裁决 / 通过 {ok} "
      f"= {ok/tot:.2f} | 自审 {len(audit_units(units))} 条")
