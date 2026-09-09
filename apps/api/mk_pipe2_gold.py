import json, collections
res = {r["id"]: r for r in json.load(open("/tmp/pipe2.json"))["results"]}
man = {}
for l in open("/tmp/pipe2_man.tsv").read().splitlines()[1:]:
    tag, grp, did, ti = l.split("\t")
    man[tag] = {"grp": grp, "title": ti}
NOTE = (
    "管线复测：**验证已发出的图层闸**。判据与 `pipes_v1` 逐字相同"
    "（固化于 `CRITERIA.md#pipes` v6），抽样改为**当前代码**（含标注层 + 别类层双闸）。"
    "**主对照通过**：12 格红线画在墨迹稀疏处，**0 格被判成管线**。"
    "（细节：其中 9 格被答成 `beam_or_grid` 而非 `nothing` —— 稀疏处常有轴线，"
    "所以这组是「稀疏且带轴线」而非纯空白，对本问题仍是有效对照。）"
    "**结果 6/40 = 15%，第一版 0/58 = 0%**。"
    "**但这 6 格判读全部标了「没把握」**；只看判读有把握的 29 格，"
    "**是管线的 0 格 = 0%**。所以 15% 完全建立在不确定判断之上。"
    "**闸确实生效的最直接证据在误检构成里**：`leader`（引出线／标注线）"
    "**从 12% 降到 0%** —— 那正是标注层闸要挡的东西。"
    "但墙与结构线仍占误检的 85%（wall 41% · beam_or_grid 44%），"
    "图层闸挡不到它们（实测 57.5% 的线段图层判不出）。"
    "**另注**：`judge_sanity` 报了 `stratum_blind`（两层众数同为 beam_or_grid），"
    "与主对照的结论不一致 —— 前者看的是类别标签，后者看的是 is_pipe，如实并记。")
by = collections.defaultdict(list)
for t, m in man.items():
    r = res[t]
    if m["grp"] == "blank":
        by["blank_control"].append({
            "ref": t, "ok": not r["is_pipe"],
            **({} if not r["is_pipe"] else {"what": "空白被判成管线"}),
            "note": f"空白对照；判读 {r['system']}"})
    else:
        by["pipe"].append({"ref": t, "ok": r["is_pipe"],
                           **({} if r["is_pipe"] else {"what": r["system"]}),
                           "note": ("判读有把握" if r["confident"] else "**判读没把握**")})
units = [{"unit": f"P2-{g}", "source": {"group": g},
          "classes": {"pipes_retest": {"method": "verdicts", "verdicts": v,
                                       "confidence": 1.0, "verified_by": ["gpt"],
                                       "criteria": "CRITERIA.md#pipes",
                                       "note": NOTE}}}
         for g, v in sorted(by.items())]
tot = sum(len(u["classes"]["pipes_retest"]["verdicts"]) for u in units)
ok = sum(1 for u in units for x in u["classes"]["pipes_retest"]["verdicts"] if x["ok"])
json.dump({"version": 1, "object_classes": ["pipes_retest"], "units": units},
          open("/app/data/model3d/gold/pipes_retest_v1.json", "w"),
          ensure_ascii=False, indent=1)
from core.model3d.gold.audit import audit_units
print(f"pipes_retest_v1.json  {len(units)} 单元 / {tot} 裁决 / 通过 {ok} "
      f"= {ok/tot:.2f} | 自审 {len(audit_units(units))} 条")
