"""学习分析器:从人工标注事件里提炼可落地的系统改进建议。

**闭环**:人每改一次,系统就多知道一点自己错在哪。本模块是这个闭环的大脑——
输入是标注事件(人给的值 vs 系统当时的自动值),输出是**具体、可核对、可执行**的建议。

**设计底线(决定这套东西有没有用)**:

1. **建议必须带证据**:每条都附样本与计数,人能当场核对凭什么这么说;
2. **区分可自动生效与需开发介入**:词表扩充、阈值调整这类采纳即生效;
   算法改造类只能导出给开发,**绝不假装已解决**;
3. **不够的证据不出建议**:样本量低于阈值就不提——凭一两次标注推规则会引入噪声,
   反而拉低下一轮准确率。

每个分析器都是纯函数:输入事件列表,输出建议列表,可离线测。
"""
from __future__ import annotations

from collections import Counter, defaultdict

#: 出建议的最低样本量:低于此值的模式很可能是偶然
MIN_EVIDENCE = 3

#: 证据里最多附几个样本(够人核对即可,不刷屏)
MAX_SAMPLES = 5


def _suggestion(
    category: str, title: str, detail: str, evidence: dict,
    impact: int, confidence: float, auto_applicable: bool,
) -> dict:
    return {
        "category": category, "title": title, "detail": detail,
        "evidence": evidence, "impact": impact,
        "confidence": round(min(max(confidence, 0.0), 1.0), 3),
        "auto_applicable": auto_applicable,
    }


def analyze_vocabulary(events: list[dict]) -> list[dict]:
    """人填的专业值系统词表里没有 → 建议扩充词表(采纳即生效)。

    这是最直接的学习:同一个词被人填了 N 次,说明它就是个真实专业名,
    只是词表没收录。收录后下一轮自动识别就能认出来。
    """
    counts: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    for e in events:
        if e.get("kind") != "discipline":
            continue
        human = (e.get("human_value") or "").strip()
        if not human or e.get("auto_value"):
            continue                    # 系统本来就认出来了,不是学习信号
        counts[human] += 1
        raw = ((e.get("context_json") or {}).get("raw_text") or "").strip()
        if raw and len(samples[human]) < MAX_SAMPLES:
            samples[human].append(raw)

    out: list[dict] = []
    for word, n in counts.most_common():
        if n < MIN_EVIDENCE:
            continue
        out.append(_suggestion(
            "vocabulary",
            f"专业词表补录「{word}」",
            f"人工填写「{word}」{n} 次,系统词表内没有该词,自动识别一直读不出。"
            f"补录后同类图纸下一轮即可自动识别。",
            {"word": word, "times": n, "raw_samples": samples.get(word, [])},
            impact=n, confidence=min(0.5 + n * 0.1, 0.95), auto_applicable=True))
    return out


def analyze_ocr_corrections(events: list[dict]) -> list[dict]:
    """OCR 原文与人工定值的稳定映射 → 建议加入纠错词典(采纳即生效)。

    实测「建筑」被 OCR 认成「建 个人」。同一错法重复出现就是可修的系统性偏差,
    不是随机噪声。
    """
    pairs: Counter[tuple[str, str]] = Counter()
    for e in events:
        human = (e.get("human_value") or "").strip()
        raw = ((e.get("context_json") or {}).get("raw_text") or "").strip()
        if not human or not raw or raw == human:
            continue
        pairs[(raw, human)] += 1

    out: list[dict] = []
    for (raw, human), n in pairs.most_common():
        if n < MIN_EVIDENCE:
            continue
        out.append(_suggestion(
            "ocr_correction",
            f"OCR 纠错「{raw}」→「{human}」",
            f"识别原文「{raw}」被人工订正为「{human}」共 {n} 次,是稳定的系统性误识,"
            f"不是随机噪声。加入纠错词典后同样的糊字可自动还原。",
            {"raw": raw, "corrected": human, "times": n},
            impact=n, confidence=min(0.4 + n * 0.12, 0.9), auto_applicable=True))
    return out


def analyze_manual_axis_drawing(events: list[dict]) -> list[dict]:
    """人频繁**手描**轴线(而非点选候选)→ 候选线抽取太严,建议放宽阈值。

    手描比例高说明自动候选没覆盖到人要的线,阈值该往松调——这是可量化的参数问题,
    采纳即生效。
    """
    total = 0
    handdrawn = 0
    spans: list[float] = []
    for e in events:
        if e.get("kind") != "axis":
            continue
        total += 1
        ctx = e.get("context_json") or {}
        if ctx.get("source") == "handdrawn":
            handdrawn += 1
            span = ctx.get("span")
            if isinstance(span, (int, float)):
                spans.append(float(span))
    if total < MIN_EVIDENCE or not handdrawn:
        return []
    ratio = handdrawn / total
    if ratio < 0.4:
        return []                      # 多数还是点选的,阈值没问题

    suggested = round(min(spans) * 0.9, 3) if spans else 0.15
    return [_suggestion(
        "threshold",
        f"放宽候选轴线最小跨度至 {suggested}",
        f"{total} 次轴线标注里有 {handdrawn} 次是手描的（{ratio:.0%}）,"
        f"说明自动候选没覆盖到人要的线。手描线的最小跨度为 "
        f"{min(spans) if spans else '未知'},建议把门槛降到 {suggested} 以纳入这类线。",
        {"total": total, "handdrawn": handdrawn, "ratio": round(ratio, 3),
         "observed_spans": sorted(spans)[:MAX_SAMPLES]},
        impact=handdrawn, confidence=min(0.4 + ratio * 0.5, 0.9),
        auto_applicable=True)]


def analyze_template_gaps(events: list[dict]) -> list[dict]:
    """同一版式反复要人框选 → 图框版式识别有缺口,需开发介入(不能自动修)。

    页面宽高比相同却总要重框,说明「同版式」判据不够——只看宽高比区分不了
    图框内部布局。这属于算法改造,导出给开发,**不假装采纳即可解决**。
    """
    by_aspect: Counter[str] = Counter()
    for e in events:
        if e.get("kind") != "title_block":
            continue
        aspect = (e.get("context_json") or {}).get("page_aspect")
        if aspect is not None:
            by_aspect[str(aspect)] += 1

    out: list[dict] = []
    for aspect, n in by_aspect.most_common():
        if n < MIN_EVIDENCE * 2:       # 版式类问题需要更强证据
            continue
        out.append(_suggestion(
            "algorithm",
            f"同版式(宽高比 {aspect})仍需反复框选 {n} 次",
            "页面宽高比相同却总要重框,说明「同版式」判据不足——宽高比区分不了图框"
            "内部布局。需要更强的版式指纹(如图框线位置聚类),属算法改造,"
            "**不能靠采纳建议当场解决**,建议导出交开发。",
            {"page_aspect": aspect, "manual_times": n},
            impact=n, confidence=0.7, auto_applicable=False))
    return out


def analyze_scale_disagreement(events: list[dict]) -> list[dict]:
    """人确认的比例尺与系统自动值系统性不一致 → 需开发核查换算链路。"""
    mismatched = [
        e for e in events
        if e.get("kind") == "scale" and e.get("auto_value")
        and e.get("human_value") and e["auto_value"] != e["human_value"]
    ]
    if len(mismatched) < MIN_EVIDENCE:
        return []
    samples = [{"auto": e["auto_value"], "human": e["human_value"]}
               for e in mismatched[:MAX_SAMPLES]]
    return [_suggestion(
        "algorithm",
        f"比例尺自动值与人工确认不一致 {len(mismatched)} 次",
        "自动读出的比例尺被人工改掉,说明换算链路或候选选取有系统性偏差。"
        "需要开发核对「图上 1:N → 米/点」的换算与候选排序,不宜自动改。",
        {"count": len(mismatched), "samples": samples},
        impact=len(mismatched), confidence=0.6, auto_applicable=False)]


#: 全部分析器。新增学习维度只要往这里加一个纯函数。
ANALYZERS = (
    analyze_vocabulary,
    analyze_ocr_corrections,
    analyze_manual_axis_drawing,
    analyze_template_gaps,
    analyze_scale_disagreement,
)


def run_analyzers(events: list[dict]) -> list[dict]:
    """跑全部分析器,按预估影响降序返回建议。

    单个分析器抛错不影响其余——学习是辅助功能,不该因为一个维度出错就整体瘫痪。
    """
    found: list[dict] = []
    for analyzer in ANALYZERS:
        try:
            found.extend(analyzer(events))
        except Exception:  # noqa: BLE001 — 单个分析器失败不拖垮整轮学习
            continue
    return sorted(found, key=lambda s: (-s["impact"], -s["confidence"]))
