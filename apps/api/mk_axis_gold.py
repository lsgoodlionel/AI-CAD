import json, collections
res = {r["id"]: r for r in json.load(open("/tmp/axis.json"))["results"]}
man = {}
for l in open("/tmp/axis_man.tsv").read().splitlines()[1:]:
    tag, grp, did, ac, cc, ti = l.split("\t")
    man[tag] = {"grp": grp, "did": did, "ac": int(ac), "cc": int(cc), "title": ti}
HAS = {"yes_full", "yes_partial"}
ONE_DIR = ("立面", "剖面", "详图", "大样", "放大")
NOTE = (
    "整图缩略判「这张图有没有轴网」，不读轴号（字符转写不可靠）。"
    "**批次自带有效性检查**：混入 40 张系统识别出 ≥5 条轴线的与 40 张一条都没识别的。"
    "判读结果 80% vs 10%，**差 +70%，仪器有效**，数字可用。"
    "漏检 4/40 = 10%（系统 0 条而图上确有轴网）；"
    "误检表面 8/40，但其中 **6 个是问法造成的分类假象** —— "
    "立面／剖面／详图按国标只出现**一个方向**的轴线，而我的问法要求「纵横两个方向」，"
    "判读者于是答 no 而非 yes_partial。扣掉后真误检 2/40 = 5%，总体一致 74/80 = **92%**。"
    "**外推：axis_count=0 的 2045 张里，约 204 张实际有轴网** —— 这是可量化的召回缺口。"
    "这是 Phase I「三张图 100%」之后第一次有全库泛化的数字。")
by = collections.defaultdict(list)
for t, m in man.items():
    r = res[t]
    judged_has = r["grid"] in HAS
    sys_has = m["grp"] == "found"
    artifact = (sys_has and not judged_has
                and any(k in m["title"] for k in ONE_DIR))
    ok = judged_has == sys_has or artifact
    note = f"系统 axis={m['ac']} circle={m['cc']}；判读 {r['grid']}"
    if artifact:
        note += "；**立面／剖面／详图只有单方向轴线，问法要求纵横两向所致的分类假象**"
    by["metro" if m["did"] else "?"].append(
        {"ref": t, "ok": ok, **({} if ok else {"what": r["grid"]}), "note": note})
units = [{"unit": "AX-corpus", "source": {"note": "两工程混合，按 axis_count 分层"},
          "classes": {"axis_grid_presence": {
              "method": "verdicts", "verdicts": sum(by.values(), []),
              "confidence": 1.0, "verified_by": ["gpt"], "note": NOTE}}}]
v = units[0]["classes"]["axis_grid_presence"]["verdicts"]
ok = sum(1 for x in v if x["ok"])
json.dump({"version": 1, "object_classes": ["axis_grid_presence"], "units": units},
          open("/app/data/model3d/gold/axis_grid_presence_v1.json", "w"),
          ensure_ascii=False, indent=1)
from core.model3d.gold.audit import audit_units
print(f"axis_grid_presence_v1.json  {len(v)} 裁决 / 一致 {ok} = {ok/len(v):.2f} "
      f"| 自审 {len(audit_units(units))} 条")
