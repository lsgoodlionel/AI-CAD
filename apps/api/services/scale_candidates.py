"""比例尺候选提取(攻 `drawing_transform` 瓶颈)——**人审在环,不自动落库**。纯函数。

## 实测结论(为什么是"候选"而非自动修复)

`drawing_transform` 覆盖 704/2308(30.5%),卡住轴网定位/回投核对/金标签导出。
本轮用 704 张已有变换做**真值对照**,验证了三条自动路径,**全部不达标**:

| 路径 | 准确率(±10%) | 结论 |
|------|--------------|------|
| 尺寸链相邻标注中心距推比例 | **3.6%** | 假设在真实 OCR 数据上不成立 |
| 图幅(page_h)+专业 分组众数 | **50.9%** | 抛硬币级,不可自动落库 |
| OCR 比例尺文字直接换算 | **24–26%** | 一图多比例尺,主次难分 |

**更关键的发现**:所谓"真值"本身质量存疑——705 个已有变换**平均 confidence 仅 0.007**,
**仅 46.1% 符合标准比例尺**(±5%)。故瓶颈性质是**质量问题**,不是单纯覆盖率问题;
用不准的算法盲目提覆盖只会更糟(错变换让构件位置全错,比没有更坏)。

## 本模块做什么

提供**可信度可解释的候选**,交人确认(与 Phase H 人审范式一致):
- 从 OCR 文本提取 `1:N` 比例尺标注(施工图上明确写出的信息);
- 按**标准比例尺吸附**(1:50/100/200…),命中即精确换算 `scale = N × 25.4/72 / 1000`
  (物理关系精确:1:100 → 0.03528 m/pt);
- 给出票数/一致性,人确认后即可落**精确**变换。
"""
from __future__ import annotations

import re
from collections import Counter

#: 1pt = 25.4/72 mm(PDF 点)。比例尺 1:N → scale_m_pt = N × PT_MM / 1000
PT_MM = 25.4 / 72

#: 建筑施工图常用比例尺分母
STANDARD_DENOMINATORS = (
    5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200, 250, 300, 400, 500, 1000, 2000,
)

_SCALE_PATTERN = re.compile(r"1\s*[:：]\s*(\d{1,4})")

#: **图框区域**(领域知识):常规施工图的比例尺标注在图框内——图框是图纸外围用方框
#: 分隔出的区域,一般位于图纸**右侧**或**下侧**。图面中间的 `1:N` 多为详图局部比例尺,
#: 而图框内的才是该张图的**主比例尺**。据此给图框内候选加权,解决「一图多比例尺、
#: 主次难分」(此前不分位置直接选,真值对照准确率仅 24–26%)。
TITLE_BLOCK_RIGHT_RATIO = 0.72     # x > 页宽 × 此值 → 右侧图框带
TITLE_BLOCK_BOTTOM_RATIO = 0.80    # y > 页高 × 此值 → 下侧图框带
TITLE_BLOCK_WEIGHT = 5             # 图框内候选的票权倍数


def in_title_block(
    x: float | None, y: float | None, page_w: float | None, page_h: float | None,
) -> bool:
    """该文本是否落在图框带(页面右侧或下侧)。缺尺寸/坐标 → False(不加权)。"""
    if not page_w or not page_h or x is None or y is None:
        return False
    return (float(x) > page_w * TITLE_BLOCK_RIGHT_RATIO
            or float(y) > page_h * TITLE_BLOCK_BOTTOM_RATIO)
#: 合理比例尺分母范围(超出视为误识,如电话号码/编号)
MIN_DENOM = 5
MAX_DENOM = 2000


def extract_denominators(texts: list[str]) -> list[int]:
    """从 OCR 文本提取比例尺分母(`1:100` → 100),过滤越界值。"""
    out: list[int] = []
    for text in texts or []:
        for match in _SCALE_PATTERN.findall(str(text)):
            try:
                n = int(match)
            except ValueError:
                continue
            if MIN_DENOM <= n <= MAX_DENOM:
                out.append(n)
    return out


def scale_of_denominator(denominator: int) -> float:
    """比例尺分母 → scale(m/pt),精确物理换算(1:100 → 0.03528)。"""
    return round(denominator * PT_MM / 1000, 8)


def snap_to_standard(denominator: int, tolerance: float = 0.05) -> tuple[int, bool]:
    """吸附到最近的标准比例尺分母;相对偏差超容差则不吸附(返回原值)。"""
    best = min(STANDARD_DENOMINATORS, key=lambda s: abs(s - denominator) / s)
    if abs(best - denominator) / best <= tolerance:
        return best, True
    return denominator, False


def build_scale_candidates(
    texts: list[str],
    located: list[dict] | None = None,
    page_w: float | None = None,
    page_h: float | None = None,
) -> list[dict]:
    """OCR 文本 → 比例尺候选列表(按票数降序),供人审选择。

    每项 {denominator, scale_m_pt, votes, is_standard, share}:
    - votes:该比例尺在图上出现次数;share:占全部比例尺标注的比例;
    - is_standard:是否命中标准比例尺(命中者换算精确,优先推荐)。
    一图多比例尺很常见(主图 1:100 + 详图 1:10),**不自动选**,列出全部交人裁决。
    """
    # located(可选):[{"content", "x", "y"}] —— 带位置的文本,用于图框加权
    weighted: list[tuple[int, int]] = []      # (分母, 票权)
    if located and page_w and page_h:
        for item in located:
            in_block = in_title_block(item.get("x"), item.get("y"), page_w, page_h)
            weight = TITLE_BLOCK_WEIGHT if in_block else 1
            for denom in extract_denominators([str(item.get("content") or "")]):
                weighted.append((denom, weight))
    if not weighted:
        weighted = [(d, 1) for d in extract_denominators(texts)]
    if not weighted:
        return []
    snapped = [(snap_to_standard(d), w) for d, w in weighted]
    tally: Counter = Counter()
    std_map: dict[int, bool] = {}
    for (denom, is_std), weight in snapped:
        tally[denom] += weight
        std_map[denom] = is_std
    total = sum(tally.values())
    return [
        {
            "denominator": denom,
            "scale_m_pt": scale_of_denominator(denom),
            "votes": votes,      # 图框内候选按 TITLE_BLOCK_WEIGHT 加权后的票数
            "share": round(votes / total, 3),
            "is_standard": bool(std_map.get(denom)),
            "label": f"1:{denom}",
        }
        for denom, votes in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def origin_from_axis_points(axis_points: list[dict], page_h: float) -> tuple[float, float]:
    """轴号位置(pt)→ 变换原点(pt),与 `pt_to_meter` 同口径(y 翻转后取最小)。

    无轴号 → (0,0):以页面左下角为原点,相对定位仍可用。
    """
    xs = [float(a["x"]) for a in axis_points or [] if a.get("x") is not None]
    ys = [page_h - float(a["y"]) for a in axis_points or [] if a.get("y") is not None]
    return (min(xs) if xs else 0.0, min(ys) if ys else 0.0)


def build_confirmed_transform(
    denominator: int, axis_points: list[dict], page_h: float,
) -> dict | None:
    """人确认的比例尺 + 轴号位置 + 真实页高 → 可落库的变换。

    page_h 必须来自**真实 PDF 页面**(实测档案坐标推断不可靠:某图 page_h=1684
    而档案最大 y=2331)。denominator 非正或 page_h 缺失 → None。
    """
    if not denominator or denominator <= 0 or not page_h:
        return None
    origin_x, origin_y = origin_from_axis_points(axis_points, page_h)
    return {
        "scale_m_pt": scale_of_denominator(denominator),
        "origin_x": origin_x,
        "origin_y": origin_y,
        "page_h": float(page_h),
        "confidence": 1.0,          # 人确认 → 满置信(区别于算法估算的 0.007)
        "source": "human_confirmed_scale",
    }


def assess_existing_scale(scale_m_pt: float, tolerance: float = 0.05) -> dict:
    """评估**已有**变换的可信度:反推比例尺分母,看是否为标准值。

    实测:705 个已有变换仅 46.1% 符合标准比例尺(±5%),平均 confidence 0.007
    → 现有变换需要复核,而非当作真值。
    """
    if not scale_m_pt or scale_m_pt <= 0:
        return {"denominator": None, "is_standard": False, "deviation": None}
    denom = scale_m_pt * 1000 / PT_MM
    best = min(STANDARD_DENOMINATORS, key=lambda s: abs(s - denom) / s)
    deviation = abs(best - denom) / best
    return {
        "denominator": round(denom, 1),
        "nearest_standard": best,
        "is_standard": deviation <= tolerance,
        "deviation": round(deviation, 4),
    }
