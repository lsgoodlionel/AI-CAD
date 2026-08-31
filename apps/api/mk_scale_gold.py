import json, collections
res = {r["id"]: r for r in json.load(open("/tmp/scale.json"))["results"]}
man = {}
for l in open("/tmp/scale_man.tsv").read().splitlines()[1:]:
    tag, st, did, sc, cf, ti = l.split("\t")
    man[tag] = {"stratum": st, "did": did, "scale": float(sc),
                "conf": float(cf), "title": ti}
JUDGEABLE = {"ok", "too_long", "too_short", "way_off"}
NOTE = (
    "比例验证：每格画一条**系统认为是 8 米**的红线，判读者拿门（1m）、楼梯踏步"
    "（0.3m）、车位（2.5×5m）、座椅排距（1m）、柱距（6~9m）去比 —— **纯比较，"
    "不读尺寸数字**（字符转写是已知不可靠的能力边界）。裁剪用固定的页面比例、"
    "与 scale 无关，只有红线长度依赖 scale，所以比例错了红线就会横贯整格或缩成一点。"
    "**能判的 56 格里比例合理的只有 17 = 30%。**"
    "错向以 `too_short` 为主（26/39）—— 红线由 8米/scale 换算，太短即 scale 偏大，"
    "**意味着构件尺寸与工程量被整体放大**。"
    "**置信度携带负信息**：confidence=1.00 的合理率 24%，<1.00 的反而 37%。"
    "混淆已排除：判读与「红线占画面多大」相关（中位 1.39 / 0.80 / 0.15），"
    "但 too_short 组里 **81%** 的占比落在 ok 组区间内 —— 同占比下仍能分开，不是纯假象。")
by = collections.defaultdict(list)
for t, m in man.items():
    r = res[t]
    if r["verdict"] not in JUDGEABLE:
        continue
    good = r["verdict"] == "ok"
    by[m["stratum"]].append({
        "ref": t, "ok": good, **({} if good else {"what": r["verdict"]}),
        "note": f"scale_m_pt={m['scale']:.5f} confidence={m['conf']:.2f}"})
units = [{"unit": f"SC-{i}", "source": {"stratum": st},
          "classes": {"drawing_scale": {"method": "verdicts", "verdicts": v,
                                        "confidence": 1.0, "verified_by": ["gpt"],
                                        "criteria": "CRITERIA.md#drawing_scale",
                                        "note": NOTE}}}
         for i, (st, v) in enumerate(sorted(by.items()), 1)]
tot = sum(len(u["classes"]["drawing_scale"]["verdicts"]) for u in units)
ok = sum(1 for u in units for x in u["classes"]["drawing_scale"]["verdicts"] if x["ok"])
json.dump({"version": 1, "object_classes": ["drawing_scale"], "units": units},
          open("/app/data/model3d/gold/drawing_scale_v1.json", "w"),
          ensure_ascii=False, indent=1)
from core.model3d.gold.audit import audit_units
print(f"drawing_scale_v1.json  {len(units)} 单元 / {tot} 裁决 / 合理 {ok} "
      f"= {ok/tot:.2f} | 自审 {len(audit_units(units))} 条")
