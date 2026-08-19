"""建模角色识别——**不依赖任何具体工程的图号体系**。

上海大歌剧院用 `A-01` 轴网定位、`A-10` 完整平面、`S-x-20` 结构平面。
但那是**这一家设计院、这一个工程**的编号。把它写死，系统就只能服务一个项目。

**三级级联，越靠前越不依赖编号：**

| 级 | 依据 | 是否依赖编号 | 置信度 |
|---|---|---|---|
| 1 | **内容特征**（轴号圈 / **读出的**工程坐标 / 标高链 / 轴网带 / 构件数） | **完全不依赖** | 高 |
| 2 | **国标术语**（GB/T 50001 §3.3.1 图种名、GB/T 50104） | 不依赖（全行业通用） | 中 |
| 3 | **编号模式**（**从本批图纸学出来**，不写死） | 依赖，但是学的 | 低 |

三级都判不出 → `unknown`，**如实标注，绝不猜**。

**为什么内容压过图名**：图名会写错、会残缺（实测 OCR 把 `7-7剖面图` 读成
`8剖面图`），而轴号圈、坐标引线、标高链这些**几何指纹**不会骗人。
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass
from typing import Any, Mapping

# ── 角色（对齐 docs/MODELING_PIPELINE_BLUEPRINT.md 的 P0~P6）──
ROLE_COORDINATE_BASE = "coordinate_base"        # P0 世界坐标基准
ROLE_FLOOR_SKELETON = "floor_skeleton"          # P1 楼层骨架
ROLE_ELEVATION_REFERENCE = "elevation_reference"  # P2 楼层标高来源
ROLE_COMPONENT_SOURCE = "component_source"      # P3/P4 构件来源
ROLE_DETAIL = "detail"                          # P5 细节
ROLE_NON_GEOMETRIC = "non_geometric"            # P6 非几何
ROLE_UNKNOWN = "unknown"

#: 建模消费顺序。数字越小越先处理——这就是「效率顺序」的落点。
ROLE_STAGE = {
    ROLE_COORDINATE_BASE: 0,
    ROLE_FLOOR_SKELETON: 1,
    ROLE_ELEVATION_REFERENCE: 2,
    ROLE_COMPONENT_SOURCE: 3,
    ROLE_DETAIL: 5,
    ROLE_NON_GEOMETRIC: 6,
    ROLE_UNKNOWN: 9,
}

# ── 第 1 级：内容特征阈值（实测标定，见各常量注释）──

#: 成为坐标基准所需的轴号圈数。实测三张定位图 107~285 个。
#: 取 20：小图纸的局部定位图也要能进，但排除详图上零星几个圈。
MIN_CIRCLES_FOR_BASE = 20
#: 世界坐标变换的 **RANSAC 内点数**。这是「真读出了成组一致的工程坐标」
#: 的唯一可靠证据。
#:
#: 三个更弱的判据都被实测否掉：
#:
#: * **引线数** ——给排水管线是「水平段 + 斜段」，形状与坐标标注引线一样，
#:   `find_leaders` 会大量误检。`P-29-41 四层喷淋抗震支架平面图` 检出
#:   **64 条引线**、356 个圈，比真定位图 A-01-02A（16 条）还多。
#: * **粗错数（outliers）** ——粗错恰恰是**读错了**的证据，不是读对的。
#'  把它算进来会让一批机电图被判成坐标基准。
#: * **有没有 transform** ——**2 个点必然精确拟合**一个相似变换，
#:   实测 29 张图的 `rmse_m` 恰为 0.000、内点恰为 2，全是退化解。
#:
#: 取 5：3 点拟合相似变换只剩 2 个残差自由度，极易过拟合噪声——
#: 实测内点恰为 3 的有 21 张，绝大多数是机电图。
#:
#: 还要配 RMSE 上限：**内点多不等于拟合准**。实测
#: `ZNH-01-01 弱电室外总平面图` 内点 10 但 RMSE **0.94 米**，
#: `A-04-02B 地下一层防火分区图` 内点 6 但 RMSE **1.07 米**——都该排除。
MIN_TRANSFORM_INLIERS = 5

#: 世界坐标变换的残差上限（米）。工程定位精度量级。
#: 实测通过者：`A-01-02A` **6.5 毫米**、`IE-26-02A` 165mm、
#: `A-01-04A` 183mm、`P-01-01C` 224mm、桩位布置图 488mm。
MAX_TRANSFORM_RMSE_M = 0.5
#: 主标高链长度。实测立面/剖面 5~13 个；详图上零星 2~3 个标高不成链。
MIN_ELEVATION_CHAIN = 4
#: 楼层骨架：双向都要有轴号带（§8.0.2 编号注写在下方及左侧）。
MIN_BANDS_PER_DIRECTION = 1
#: 楼层骨架的构件密度下限——覆盖整层的平面图必然有大量构件。
MIN_COMPONENTS_FOR_SKELETON = 200

# ── 第 2 级：国标术语（GB/T 50001 §3.3.1 / GB/T 50104）──
#
# 顺序即优先级：先匹配到的胜出。**非几何**放最前，因为
# 「XX平面图说明」这类既含「平面图」又含「说明」的，本质是说明。
_TERM_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"目录|设计说明|施工说明|总说明|统一说明|修改通知|变更单"),
     ROLE_NON_GEOMETRIC),
    (re.compile(r"系统图|流程图|原理图|配电箱|接线图"), ROLE_NON_GEOMETRIC),
    (re.compile(r"(?:材料|做法|门窗|构件|设备)\s*表$|一览表|明细表"), ROLE_NON_GEOMETRIC),
    (re.compile(r"图例"), ROLE_NON_GEOMETRIC),
    # 定位图 + 轴网 —— 坐标基准
    (re.compile(r"轴网.*定位图|定位图.*轴网|轴线定位|坐标定位"), ROLE_COORDINATE_BASE),
    (re.compile(r"立面图|剖面图|断面图"), ROLE_ELEVATION_REFERENCE),
    (re.compile(r"详图|大样|放大图|节点图|构造节点"), ROLE_DETAIL),
    # 「完整平面图」「总平面」这类覆盖整层的 —— 楼层骨架
    (re.compile(r"完整平面图|平面总图|整体平面"), ROLE_FLOOR_SKELETON),
    (re.compile(r"平面图|平面布置|平面$"), ROLE_COMPONENT_SOURCE),
    # **平法施工图**（22G101《混凝土结构施工图平面整体表示方法制图规则》）：
    # 「平面整体配筋图」「柱平法施工图」「梁配筋图」—— 结构主力图纸。
    # 实测第二工程 `S-31-18A-六层次梁平面整体配筋图` 曾判为 unknown，
    # 因为术语表只认 `平面图|平面布置|平面$`，中间夹了「整体」就三条皆不匹配。
    # 放在最后：`详图|大样|节点` 先命中，「梁配筋详图」仍归详图。
    (re.compile(r"平法|平面整体|配筋图"), ROLE_COMPONENT_SOURCE),
)

#: 楼层平面图：`N层平面…` / `NF平面…`（N 为中文或阿拉伯数字）。
#: 「平面布置」也算 —— 实测第二工程建筑专业用的正是 `2F平面布置图`。
_FLOOR_PLAN_RE = re.compile(
    r"(?:[一二三四五六七八九十百]+|\d{1,3})\s*(?:层|F)\s*平面"
    r"|首层平面|地下[一二三四五六七八九十\d]+层平面")


# ── 第 3 级：编号模式 ──

#: 编号段：去掉最后一节流水号。`A-10-04C` → `A-10`；`JZ-SG-01-02` → `JZ-SG-01`。
#: **不假定任何具体前缀**，只假定「用分隔符分节、最后一节是流水号」这个
#: 跨行业通用的习惯。
_SEGMENT_RE = re.compile(r"^(.+)[-_.\s][^-_.\s]+$")

#: 一个编号段至少要有这么多张图才值得归纳规律。
MIN_SAMPLES_PER_SEGMENT = 3
#: 段内角色一致性下限。低于它说明该段是混装的，学不出规律。
MIN_SEGMENT_PURITY = 0.8

# ── 置信度：必须体现「内容 > 术语 > 编号模式」──
CONF_CONTENT = 0.92
CONF_TERM = 0.75
CONF_PATTERN = 0.55
CONF_UNKNOWN = 0.2


@dataclass(frozen=True)
class RoleResult:
    """角色判别结果。`source` 说明是第几级定的——便于事后追责到判据。"""
    role: str
    confidence: float
    source: str            # content | term | pattern | none
    reason: str = ""

    @property
    def stage(self) -> int:
        return ROLE_STAGE.get(self.role, ROLE_STAGE[ROLE_UNKNOWN])


def number_segment(drawing_no: str) -> str | None:
    """图号 → 编号段（去掉末节流水号）。认不出返回 ``None``。"""
    raw = str(drawing_no or "").strip()
    if not raw:
        return None
    match = _SEGMENT_RE.match(raw)
    return match.group(1) if match else None


def _by_content(evidence: Mapping[str, Any] | None) -> RoleResult | None:
    """第 1 级：内容特征。**与图号、图名都无关。**"""
    if not evidence:
        return None

    def _n(key: str) -> int:
        try:
            return int(evidence.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    circles = _n("axis_circle_count")
    # 判据是「RANSAC 求出了成组一致的世界坐标」——见 MIN_TRANSFORM_INLIERS
    inliers = _n("transform_inliers")
    try:
        rmse = float(evidence.get("transform_rmse_m") or 0.0)
    except (TypeError, ValueError):
        rmse = 0.0
    if (circles >= MIN_CIRCLES_FOR_BASE and inliers >= MIN_TRANSFORM_INLIERS
            and 0.0 <= rmse <= MAX_TRANSFORM_RMSE_M):
        return RoleResult(
            ROLE_COORDINATE_BASE, CONF_CONTENT, "content",
            f"{circles} 个轴号圈(§8.0.2) + 世界坐标变换 {inliers} 个内点、"
            f"残差 {rmse * 1000:.0f} 毫米(§11.8)")

    chain = _n("elevation_chain_length")
    if chain >= MIN_ELEVATION_CHAIN:
        return RoleResult(ROLE_ELEVATION_REFERENCE, CONF_CONTENT, "content",
                          f"竖向标高链 {chain} 个(§11.8)")

    components = _n("component_count")
    bands_x, bands_y = _n("axis_bands_x"), _n("axis_bands_y")
    has_grid = (bands_x >= MIN_BANDS_PER_DIRECTION
                and bands_y >= MIN_BANDS_PER_DIRECTION)
    if has_grid and components >= MIN_COMPONENTS_FOR_SKELETON:
        return RoleResult(ROLE_FLOOR_SKELETON, CONF_CONTENT, "content",
                          f"双向轴号带 + {components} 个构件")
    if components >= MIN_COMPONENTS_FOR_SKELETON:
        return RoleResult(ROLE_COMPONENT_SOURCE, CONF_CONTENT, "content",
                          f"{components} 个构件但轴网不完整")
    return None


def _by_term(drawing: Mapping[str, Any]) -> RoleResult | None:
    """第 2 级：国标术语。**全行业通用，不绑任何院的编号。**"""
    # **必须逐源匹配**：把 title / drawing_no 拼成一串再匹配，
    # 会让 `做法表$` 这类**行尾锚点**失效——标题后面还跟着图号。
    # 源的先后即权威性：title > drawing_no > filename。
    sources = [str(drawing.get(key) or "").strip()
               for key in ("title", "drawing_no", "filename")]
    # **建筑专业的楼层平面图 = 楼层骨架**（国标专业分工）。
    # 原规则只认「完整平面图|平面总图|整体平面」—— 那是大歌剧院这家院的
    # 措辞；轨道交通就叫「六层平面图」，实测 11 张全判成了构件来源。
    # 判据不绑措辞：建筑专业的平面图定义楼层轮廓与房间，是骨架；
    # 结构/机电/装饰的平面图表达构件与管线，是构件来源。
    if str(drawing.get("discipline") or "").lower() == "architecture":
        for text in sources:
            if _FLOOR_PLAN_RE.search(text):
                return RoleResult(
                    ROLE_FLOOR_SKELETON, CONF_TERM, "term",
                    f"建筑专业的楼层平面图「{text}」——定义楼层轮廓与房间")
    for pattern, role in _TERM_RULES:
        for text in sources:
            if text and pattern.search(text):
                return RoleResult(role, CONF_TERM, "term",
                                  f"国标术语命中:{pattern.pattern[:24]}")
    return None


def _by_pattern(drawing: Mapping[str, Any],
                patterns: Mapping[str, str] | None) -> RoleResult | None:
    """第 3 级：本批图纸学到的编号规律。"""
    if not patterns:
        return None
    segment = number_segment(drawing.get("drawing_no", ""))
    role = patterns.get(segment) if segment else None
    if role:
        return RoleResult(role, CONF_PATTERN, "pattern",
                          f"本批图纸中编号段 {segment} 的多数角色")
    return None


def classify_role(drawing: Mapping[str, Any], *,
                  evidence: Mapping[str, Any] | None = None,
                  patterns: Mapping[str, str] | None = None) -> RoleResult:
    """判定一张图的建模角色。三级级联，判不出如实返回 `unknown`。"""
    for result in (_by_content(evidence), _by_term(drawing),
                   _by_pattern(drawing, patterns)):
        if result is not None:
            return result
    return RoleResult(ROLE_UNKNOWN, CONF_UNKNOWN, "none",
                      "内容、国标术语、编号模式三级均未命中")


def learn_number_patterns(
    labelled: list[tuple[Mapping[str, Any], str]], *,
    min_samples: int = MIN_SAMPLES_PER_SEGMENT,
    min_purity: float = MIN_SEGMENT_PURITY,
) -> dict[str, str]:
    """从**本批已定角色的图纸**归纳「编号段 → 角色」。

    这是第 3 级的来源。**不硬编码任何前缀**——换一个工程用
    `JZ-SG-01` 也一样学得到。

    段内角色不一致（纯度低于 `min_purity`）时**不学**——
    混装的编号段学出来只会误导。
    """
    buckets: dict[str, list[str]] = collections.defaultdict(list)
    for drawing, role in labelled:
        if role in (ROLE_UNKNOWN, None):
            continue
        segment = number_segment(drawing.get("drawing_no", ""))
        if segment:
            buckets[segment].append(role)

    learned: dict[str, str] = {}
    for segment, roles in buckets.items():
        if len(roles) < min_samples:
            continue
        role, count = collections.Counter(roles).most_common(1)[0]
        if count / len(roles) >= min_purity:
            learned[segment] = role
    return learned
