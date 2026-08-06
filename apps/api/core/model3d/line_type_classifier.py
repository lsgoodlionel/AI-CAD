"""线型识别:从划-空节奏反推 CAD 线型,据此判定图元用途。

**为什么必须做线型**:GB/T 50001 用线型区分用途——轴线是**点划线(一长一短)**、
外轮廓是**双点划线(一长两短)**、构件边线是**实线**。有了线型,判定轴线就从
「几何启发式猜」变成「按制图标准读」,可靠性差一个量级。

**为什么不能直接读 PDF 的 dash 属性**:实测这三张图 `get_drawings()` 的
`dashes` 全是空——出 PDF 时虚线被**炸成了独立短线段**(每个划一条实线)。
所以要把同一直线上的碎段按位置排序,还原「划长/空长」序列,再认节奏。

**实测节奏**(A-01-02A 的轴线,offset=1588.9):

    划长  6.6, 2.1, 10.6, 2.1, 10.5, 2.1, 10.5, 2.1, ...
    空长  2.1, 2.1,  2.1, 2.1,  2.1, 2.1,  2.1, 2.1, ...

长划 10.5pt、短划 2.1pt、空隙 2.1pt 严格交替 —— 教科书式的点划线。
"""
from __future__ import annotations

#: 线型分类结果
SOLID = "solid"              # 实线:构件轮廓、图框
DASH_DOT = "dash_dot"        # 点划线(一长一短):**轴线**
DASH_DOT_DOT = "dash_dot_dot"  # 双点划线(一长两短):外轮廓/用地界线
DASHED = "dashed"            # 虚线(等长划):不可见轮廓
UNKNOWN = "unknown"

#: 判定为「同一条线上的碎段」的最小段数——太少看不出节奏
MIN_SEGMENTS_FOR_RHYTHM = 6

#: 长划与短划的长度比下限。实测 10.5 / 2.1 = 5.0,取 2.0 留足余量
LONG_SHORT_RATIO = 2.0

#: 划长归为「同一类」的相对容差。实测同类划长 10.5~10.6,差异 <1%
LENGTH_TOLERANCE = 0.25

#: 实线判定:单段或极少段且覆盖率接近满
SOLID_COVERAGE = 0.95

#: 线型判定的覆盖率下限。低于此值不是线,而是**散布的笔画**。
#: 实测依据(A-01-02A):图框会签栏的**描边文字**会被误判成双点长画线
#: ——文字笔画恰好是「两类长度 + 短划多于长划」。区分特征是覆盖率:
#: 真双点长画线 0.20~0.43,而文字只有 **0.00~0.02**,相差一个数量级。
MIN_LINE_COVERAGE = 0.05


def _cluster_lengths(values: list[float], tol: float = LENGTH_TOLERANCE) -> list[dict]:
    """把长度聚成几类(长划/短划),返回 [{mean, count}] 按均值降序。"""
    groups: list[list[float]] = []
    for v in sorted(values, reverse=True):
        placed = False
        for g in groups:
            ref = sum(g) / len(g)
            if ref > 0 and abs(v - ref) / ref <= tol:
                g.append(v)
                placed = True
                break
        if not placed:
            groups.append([v])
    return sorted(
        ({"mean": round(sum(g) / len(g), 2), "count": len(g)} for g in groups),
        key=lambda d: -d["mean"],
    )


def dash_rhythm(spans: list[tuple[float, float]]) -> dict:
    """同一直线上的碎段(沿线区间列表)→ 划长/空长序列与其分类。

    spans 为 [(lo, hi)],沿线一维坐标。返回
    {dashes, gaps, dash_classes, gap_mean, coverage}。
    """
    ordered = sorted(spans)
    dashes = [round(hi - lo, 2) for lo, hi in ordered if hi > lo]
    gaps = [round(ordered[i + 1][0] - ordered[i][1], 2)
            for i in range(len(ordered) - 1)]
    gaps = [g for g in gaps if g > 0.05]
    envelope = (ordered[-1][1] - ordered[0][0]) if ordered else 0.0
    coverage = (sum(dashes) / envelope) if envelope > 0 else 0.0
    return {
        "dashes": dashes, "gaps": gaps,
        "dash_classes": _cluster_lengths(dashes),
        "gap_mean": round(sum(gaps) / len(gaps), 2) if gaps else 0.0,
        "coverage": round(min(coverage, 1.0), 3),
    }


def classify(rhythm: dict) -> str:
    """划-空节奏 → 线型。

    判据(按制图标准的语义,而非拍出来的魔数):
    - **实线**:段少且覆盖接近满;
    - **点划线**:恰好两类划长,长短比 ≥2 —— 一长一短,**轴线**;
    - **双点划线**:两类划长,但短划数量约为长划的 2 倍 —— 一长两短,**外轮廓**;
    - **虚线**:只有一类划长(等长划)。
    """
    dashes = rhythm.get("dashes") or []
    classes = rhythm.get("dash_classes") or []
    if not dashes:
        return UNKNOWN
    if len(dashes) < MIN_SEGMENTS_FOR_RHYTHM:
        return SOLID if rhythm.get("coverage", 0) >= SOLID_COVERAGE else UNKNOWN

    if len(classes) == 1:
        # 等长划:实线(覆盖满)或虚线
        return SOLID if rhythm.get("coverage", 0) >= SOLID_COVERAGE else DASHED

    if rhythm.get("coverage", 0) < MIN_LINE_COVERAGE:
        # 沿包络的墨迹占比过低:是散布笔画(描边文字/图例),不是一根线
        return UNKNOWN

    long_cls, short_cls = classes[0], classes[-1]
    if short_cls["mean"] <= 0:
        return UNKNOWN
    if long_cls["mean"] / short_cls["mean"] < LONG_SHORT_RATIO:
        return DASHED           # 长短差别不大,算等长虚线

    ratio = short_cls["count"] / max(long_cls["count"], 1)
    # 一长两短 → 短划约为长划的 2 倍;一长一短 → 约 1 倍
    return DASH_DOT_DOT if ratio >= 1.6 else DASH_DOT


def purpose_of(line_type: str) -> str:
    """线型 → 制图语义(GB/T 50001 §4.0.2)。

    映射表的单一来源在 `drawing_conventions.LINE_TYPE_PURPOSE`。
    """
    from core.model3d.drawing_conventions import purpose_of_line_type

    return purpose_of_line_type(line_type)


def classify_spans(spans: list[tuple[float, float]]) -> dict:
    """一步到位:碎段 → {line_type, purpose, rhythm}。"""
    rhythm = dash_rhythm(spans)
    line_type = classify(rhythm)
    return {"line_type": line_type, "purpose": purpose_of(line_type),
            "rhythm": rhythm}
