"""合理性判据所用**数学/物理公式的单一真相源**：每条都要说得出出处。

规范那一侧已经立了规矩（`codes.py`：限值带条款号与原文摘录，查不到就标
`UNVERIFIED` 不编数）。数学这一侧此前是空白 —— 规则的 `basis` 里写着
「鞋带公式」「Jordan 曲线定理」「i=b/√12」，但它们**只是名字**，
没有一处可回溯的原文。一条判据若说不出「凭哪条定理、在哪本书第几页」，
它和拍脑袋的区别只是听起来更专业。

**出处怎么写**：`<key> p.<PDF 页>`，key 见 `core/knowledge/math_sources.py`。
**页码是 PDF 页**（缓存 `book.md` 里的 `## p.N` 锚点），不是书上印的页码 ——
实测 Strang 第 283 个 PDF 页印的是 273 页，两者差 10。引用要能被复现，
所以记能在缓存里定位到的那个。EPUB（`pbm-animation`）没有固定页，
按 spine 的**节**引用：`pbm-animation 节19`。

**转写会有瑕疵**：数学排版的文本层抽取不干净（实测 `det A` 抽成 `<let A`、
`A⁻¹` 抽成 `A- 1`、`|ad−bc|` 抽成 `lad-bcl`、`∮` 抽成 `I`、`∬` 抽成 `Z Z`）。
`quote` 保留抽出来的原样（便于检索定位），`expression` 写规范化的公式 ——
两者并列，不互相冒充。

**查不到就标 `UNCITED:`** 并写清查过哪几本、用了什么关键词。
带 `UNCITED` 的公式**不得用于 `impossible` 档结论** —— 那一档的含义是
「数学或物理上不可能」，凭一条找不到出处的公式说「不可能」，
是把没有依据说成了最强的依据。由 `tests/test_plausibility_formulas.py` 强制。

**本轮取证（2026-09-16）的结论**，先说清能力边界：

- **几何**站得住：行列式即面积、Green 定理（连同「简单 + 正向」这两个
  成立条件的原文定义）、凸包即最小凸集、最小二乘、RANSAC，都有原文。
- **力学只补上了一条**：Knight《Physics》定向 OCR 入库后（`knight-physics`，
  **只有 §6.1 / §12.4 / §12.8 / §15.6 四节共 29 页有正文，其余 1421 页仍是空白**），
  `mechanics.static_equilibrium` 拿到了逐字出处（p.392）。
  其余六条 —— 截面惯性矩 `bh³/12`、回转半径、长细比、欧拉临界力、`qL²/8`、
  从属面积法 —— **仍全部 UNCITED**：它们属于材料力学/结构力学，
  而已入库的两本力学材料一本是计算机动画取向的刚体动力学、
  一本是普通物理。两者的 `moment of inertia` 都是**质量**惯性矩
  （∫r²dm，kg·m²），与截面的**面积**二次矩（m⁴）不是一回事 ——
  名字像、量纲都不同，**不能互相冒充**。
- 三条几何算法（射线法判内外、Sutherland–Hodgman、旋转卡壳最小面积矩形）
  也 UNCITED —— 这批书要么不讲（`pbm-animation 节22` 明说
  "We will not discuss the process here"），要么讲的是另一个算法。
"""
from __future__ import annotations

from dataclasses import dataclass

#: 非文献来源标记
DERIVED = "DERIVED"          # 由本表其他条目推出（`derived_from` 必填）
UNCITED = "UNCITED"          # 在已入库的教材里没找到


@dataclass(frozen=True)
class Formula:
    """一条公式/定理及其出处。"""
    key: str
    name: str                    # 中文名（英文名）
    expression: str              # 规范化表达式，单行
    symbols: str                 # 符号含义与单位 —— 没有单位的公式没法复核
    conditions: str              # **成立条件**。这一栏最容易被忽略，也最容易出事
    source: str                  # "strang-la p.283" / DERIVED / "UNCITED:查过…"
    quote: str = ""              # 原文抽取样（含瑕疵，便于回到缓存里定位）
    derived_from: tuple[str, ...] = ()
    used_in: tuple[str, ...] = ()  # 哪些规则/函数用它

    @property
    def cited(self) -> bool:
        if self.source.startswith(UNCITED):
            return False
        if self.source == DERIVED:
            return bool(self.derived_from)
        return bool(self.quote)


def formula(key: str) -> Formula:
    if key not in FORMULAS:
        raise KeyError(f"未登记的公式 {key!r} —— 先登记出处再用")
    return FORMULAS[key]


def cite(key: str) -> str:
    """→ 可直接写进 `Finding.basis` 的一行引用。"""
    item = formula(key)
    return f"{item.name}：{item.expression}（{item.source}）"


def uncited_keys() -> tuple[str, ...]:
    return tuple(sorted(k for k, v in FORMULAS.items() if not v.cited))


def _cited(key: str, name: str, expression: str, symbols: str, conditions: str,
           source: str, quote: str, used_in: tuple[str, ...] = ()) -> Formula:
    return Formula(key=key, name=name, expression=expression, symbols=symbols,
                   conditions=conditions, source=source, quote=quote,
                   used_in=used_in)


def _derived(key: str, name: str, expression: str, symbols: str, conditions: str,
             derived_from: tuple[str, ...],
             used_in: tuple[str, ...] = ()) -> Formula:
    return Formula(key=key, name=name, expression=expression, symbols=symbols,
                   conditions=conditions, source=DERIVED,
                   derived_from=derived_from, used_in=used_in)


def _uncited(key: str, name: str, expression: str, symbols: str, conditions: str,
             searched: str, used_in: tuple[str, ...] = ()) -> Formula:
    """`searched` 必须写明**查过哪几本、用了什么关键词** —— 否则下一个人会重查一遍。"""
    return Formula(key=key, name=name, expression=expression, symbols=symbols,
                   conditions=conditions, source=f"{UNCITED}:{searched}",
                   used_in=used_in)


#: ── 公式表 ────────────────────────────────────────────────────────
FORMULAS: dict[str, Formula] = {
    # ── 几何 ────────────────────────────────────────────────────────
    "geometry.determinant_is_area": _cited(
        "geometry.determinant_is_area", "行列式即面积（determinant as area）",
        "S = |det[[a, b], [c, d]]| = |ad − bc|",
        "a,b,c,d：两个边向量的分量（m）；S：平行四边形面积（m²）",
        "二维；四角为 (0,0)、(a,b)、(c,d)、(a+c,b+d)，即两向量自原点出发。"
        "书上同页给了三维推广（体积 = |det A|，A 的行是盒子的棱）。",
        "strang-la p.283",
        "3 Area of parallelogram= lad-bcl if the four corners are (0, 0), (a, b), "
        "(c, d), and (a+c, b+d).\n"
        "4 Volume of box= ldet Al if the rows of A (or the columns of A) give the "
        "sides of the box.",
        ("core.model3d.plausibility.geometry.polygon_area",)),

    # Green 定理是鞋带公式、形心公式的共同上位依据，且它把「简单」「正向」
    # 这两个成立条件写成了可引用的定义 —— 单列一条，让下面两条的 DERIVED
    # 有实在的父节点，而不是把推论挂到一个并不写它的页码上。
    "geometry.greens_theorem": _cited(
        "geometry.greens_theorem", "Green（Green–Ostrogradski）定理",
        "∮_C f dx + g dy = ∬_D (∂g/∂x − ∂f/∂y) dA",
        "C：区域 D 的边界曲线；f,g：向量场分量；dA：面积元（m²）",
        "**书上把成立条件写死了三条**：D 有界、边界 C 为**闭曲线**、且**简单**"
        "（除首尾点外不自交）、**正向**（逆时针）；f、g 连续可微。"
        "同页给了『简单』的定义：参数化一一对应，r(t₁)=r(t₂) 只在 t₁=a、t₂=b 时成立。",
        "engineering-math p.319",
        "Theorem 40. (Green-Ostrogradski) Let D be a bounded domain in the\n"
        "plane whose boundary C is a closed, simple and positively oriented curve. Let\n"
        "F = (f, g) be a vector ﬁeld whose components are continuously diﬀerentiable.\n"
        "Then\nI\nf(x, y)dx + g(x, y)dy =\nZ Z\nD\n( ∂g\n∂x −∂f\n∂y )dA.\n(3.87)\n"
        "— 同页：A closed curve is called simple if it does not intersect itself "
        "except for the initial and ﬁnal point",
        ()),

    "geometry.shoelace_area": _cited(
        "geometry.shoelace_area", "鞋带公式（shoelace / surveyor's formula）",
        "2A = Σᵢ (xᵢ·yᵢ₊₁ − xᵢ₊₁·yᵢ)",
        "(xᵢ,yᵢ)：顶点坐标（m）；A：带符号面积（m²），正为逆时针",
        "**简单多边形**（不自交）；顶点按序首尾相接。"
        "**与骨架的出入**：书上给的是 n=3（三角形）的展开式（式 9），"
        "不是任意 n 边形的通式 —— 一般情形是同一恒等式对三角剖分求和，"
        "其成立条件（简单 + 正向）由 `geometry.greens_theorem` 的原文给出。"
        "『shoelace』『surveyor』这两个名字在六本里一次都没出现。",
        "strang-la p.287",
        "+½(x1Y2 - X2Y1)\n+½ (x2y3 - X3y2) \n+½(x3y1 - X1Y3).\n(9)",
        ("core.model3d.ring_order.polygon_area", "geom.zero_area_with_extent")),

    "geometry.signed_area_cancellation": _derived(
        "geometry.signed_area_cancellation", "自交环的面积相消",
        "A = A₁ + A₂，且自交时 sgn(A₁) = −sgn(A₂)",
        "A₁,A₂：自交点分出的两瓣的带符号面积（m²）",
        "环自交；两瓣绕向相反。"
        "**符号可为负、且负号照样进和式**这一点 strang-la p.287 有明文："
        "「If ( 0, 0) is outside the triangle, two of the special areas can be "
        "negative-but the sum is still correct.」；绕向反转即行列式换行、符号取反"
        "（同页性质 2）。**「蝴蝶结」两瓣恰好抵消为 0 是这两条的推论，书上没有直接写**。",
        ("geometry.shoelace_area", "geometry.determinant_is_area"),
        ("geom.self_intersecting_outline",)),

    "geometry.jordan_curve_ray_casting": _uncited(
        "geometry.jordan_curve_ray_casting", "射线法判点在多边形内（Jordan 曲线定理）",
        "点在内 ⇔ 自该点出发的任一射线与边界的交点数为奇数",
        "交点数按重数计；射线不过顶点",
        "简单闭曲线；射线与边界不相切、不过顶点（实现中需处理退化）。"
        "**最接近的两处都不是它**：① `pbm-animation 节13` 给的内外判别是"
        "「逐边取 2×2 行列式、符号全同则在内」，且**只对凸多边形成立**"
        "（脚注原文：「This algorithm will not work correctly for a concave polygon.」）；"
        "② `roads-geometry p.74` 的 Pasch 公理（线从三角形一边进必从另两边之一出）"
        "与 p.86 的平面分离公设（Postulate 9）是奇偶性的公理祖先，但都没有陈述射线法。",
        "strang-la/roads-geometry/pbm-animation/multiple-view-geometry/engineering-math/"
        "math-for-ml 六本全文检索 'Jordan Curve'（0 命中）、'Jordan'（只命中人名与"
        "Gauss-Jordan 消元）、'odd number of'、'point in polygon'、'inside polygon'、"
        "'crossing' 均无；该定理不在这批书里",
        ("core.model3d.plausibility.geometry.point_in_polygon",)),

    "geometry.convex_hull_monotone_chain": _cited(
        "geometry.convex_hull_monotone_chain",
        "凸包＝含点集的最小凸集（convex hull；实现用 Andrew 单调链）",
        "conv(S) = 包含 S 的最小凸集；凸：B 内任两点的连线段整段落在 B 内",
        "顶点坐标（m）；A_hull：凸包面积（m²）",
        "点集有限。**与骨架的出入**：**单调链算法本身书上没有** —— "
        "`pbm-animation 节22` 讲凸包时明说「Computing a convex hull is a "
        "long-standing problem ... We will not discuss the process here」。"
        "所以本条引的是凸包的**定义（最小凸集）**，这正是下游真正用到的性质"
        "（A ≤ A_hull、σ ≤ 1）；用哪种 O(n log n) 算法求它不影响结论，"
        "但共线点的取舍会决定包上是否留冗余顶点。",
        "multiple-view-geometry p.531",
        "A subset B of IR™ is called convex if the line segment joining any two "
        "points in B \nalso lies entirely within B. The convex hull of B, denoted B, "
        "is the smallest convex \nset containing B.",
        ("core.model3d.true_extent.convex_hull",)),

    "geometry.polygon_clipping_convex_requirement": _uncited(
        "geometry.polygon_clipping_convex_requirement",
        "Sutherland–Hodgman 裁剪的凸性要求",
        "逐边裁剪的结果正确 ⇔ **裁剪多边形为凸**",
        "被裁多边形可凹；裁剪多边形必须凸",
        "裁剪多边形非凸时结果会出现伪连接边，面积偏大。"
        "**近似但不同的一条**：`pbm-animation 节13` 的凸多边形内外判别脚注"
        "「This algorithm will not work correctly for a concave polygon.」—— "
        "同样是「凹了就不对」，但那是内外判别不是多边形裁剪，不能拿来充数。",
        "六本全文检索 'Sutherland'（只命中 multiple-view-geometry 的 "
        "[Sutherland-63] Sketchpad / DLT 参考文献，与裁剪无关）、'Hodgman'（0）、"
        "'clipping'（0）、'clip polygon'（0）；这批书没有计算机图形学裁剪一章",
        ("core.model3d.plausibility.geometry.polygon_overlap_area",)),

    "geometry.min_area_rect": _uncited(
        "geometry.min_area_rect", "最小面积外接矩形（rotating calipers）",
        "最小面积外接矩形必有一边与凸包的某条边共线",
        "边长（m）",
        "凸包非退化。**最接近的一处反而是反例**：`pbm-animation 节22` 谈 OBB 时说"
        "「Finding an optimally minimal box is an ill-defined problem」"
        "（三维下最小化体积还是最大边长本身没定义清），并改用 PCA 主轴求近似包围盒 —— "
        "那是三维近似解，不是二维的 Freeman–Shapira 定理。"
        "**下游影响要说清**：`true_extent` 与 `dim.column_section_below_code` 靠这条，"
        "在补上出处前它们不能出 `impossible`。",
        "六本全文检索 'rotating caliper'（0）、'minimum area rect'（0）、"
        "'oriented bounding box' / 'OBB'（只命中 pbm-animation 节22 的三维近似包围盒）；"
        "该定理属计算几何专著（Toussaint 1983），不在这批书里",
        ("core.model3d.true_extent.min_area_rect", "dim.column_section_below_code")),

    "geometry.polygon_centroid": _derived(
        "geometry.polygon_centroid", "多边形形心",
        "Cx = (1/6A)·Σ (xᵢ+xᵢ₊₁)(xᵢyᵢ₊₁ − xᵢ₊₁yᵢ)",
        "A：带符号面积（m²）；C：形心坐标（m）",
        "简单多边形；A ≠ 0（承自 Green 定理的『闭 + 简单 + 正向』）。"
        "取 f=0、g=x²/2 代入 Green 定理得 ∬_D x dA = ½∮ x² dy，再除以 A 即得；"
        "**书上没有给这个闭式** —— engineering-math p.339 只给了曲线（wire）的"
        "一次矩与质心的线积分定义，roads-geometry p.150 的 centroid 是三角形中线交点。",
        ("geometry.greens_theorem",),
        ("core.model3d.plausibility.geometry.centroid",)),

    "geometry.solidity": _derived(
        "geometry.solidity", "实心度（solidity）",
        "σ = A / A_hull ∈ (0, 1]",
        "A：多边形面积；A_hull：其凸包面积（m²）",
        "A_hull > 0。σ ≤ 1 直接来自凸包的最小性（凸包包含原多边形，故面积不小于它）；"
        "**σ 这个形状描述子本身不在这批书里**，是上面那条定义的直接推论。"
        "注意 σ 对自交轮廓无意义 —— 分子那个 A 已经被正负相消吃掉了。",
        ("geometry.convex_hull_monotone_chain",),
        ("geom.low_solidity",)),

    # ── 力学 ────────────────────────────────────────────────────────
    # 这一族是本轮取证最薄的一块：七条里六条 UNCITED。原因不是没查 ——
    # Knight《Physics》定向 OCR 的四节（§6.1 平衡、§12.4 惯性矩、§12.8 刚体静力平衡、
    # §15.6 弹性）覆盖的是**普通物理**，材料力学/结构力学的截面几何量与梁柱内力
    # 不在其中；`pbm-animation` 则是计算机动画取向的刚体动力学。
    "mechanics.static_equilibrium": _cited(
        "mechanics.static_equilibrium", "静力平衡（static equilibrium）",
        "ΣF = 0 且 ΣM = 0",
        "F：力（N）；M（书中记 τ）：对任一点的力矩（N·m）",
        "**刚体**；准静态。三条必须一起读的限定，书上都有明文："
        "① **质点的平衡条件不够用** —— 同书 p.184 明说"
        "「The equilibrium condition of Equations 6.1 applies only to particles, "
        "which cannot rotate. Equilibrium of an extended object, which can rotate, "
        "requres an additional condition.」，柱、梁都是可转动的扩展体，"
        "只验 ΣF=0 会漏掉倾覆；② **力矩对任一点取都为零**（p.392："
        "「For a rigid body in total equilibrium, there is no net torque about any "
        "point.」），所以支点可以随便选；③ 静止与匀速在牛顿视角下同解"
        "（p.184：static 与 dynamic equilibrium「are identical from a Newtonian "
        "perspective because Fnet = 0 and a = 0」）—— 判据用的是其逆否："
        "**有净力/净力矩 ⇒ 状态必变 ⇒ 待不住**。"
        "同一段还点名这正是本项目的场景：statics「analyzes buildings, dams, "
        "bridges, and other structures in total static equilibrium」。"
        "`pbm-animation 节19` 的『匀速平动无净力、匀角速转动无净力矩』与之同义，"
        "节21 另给了重力恒在（gravity 属 body force，作用于每个质点）。"
        "**这是 OCR 文本**（置信 0.96）：原文把 `F_net` 抽成 `Fet`/`Ft`、"
        "`τ_net` 抽成 `τnet`/`τ`，`quote` 照原样留着好回缓存里定位。",
        "knight-physics p.392",
        "We now have two versions of Newton's second law: Fet = Ma for translational\n"
        "motion and τnet = Iα for rotational motion. The condition for a rigid body "
        "to be in\nstatic equilibrium is both Ft = 0 and τ = 0. That is, no net force "
        "and no net\ntorque. An important branch of engineering called statics "
        "analyzes buildings, dams,\nbridges, and other structures in total static "
        "equilibrium.\n"
        "— 同页解题策略：Write equations for ΣFx = 0, ΣFy = 0, and Στ = 0.",
        ("support.floating_column", "support.beam_without_support")),

    "mechanics.second_moment_rectangle": _uncited(
        "mechanics.second_moment_rectangle", "矩形截面惯性矩",
        "I = b·h³/12",
        "b：截面宽（m）；h：截面高（m）；I：惯性矩（m⁴）",
        "矩形截面；绕形心轴。**一个必须点破的混淆**：Knight §12.4"
        "（`knight-physics` p.379-385，专讲 Calculating Moment of Inertia）、"
        "`pbm-animation` 节19/节21、`engineering-math p.340` 里的 "
        "'moment of inertia' **全都是质量惯性矩**（∫r²dm，kg·m²；Knight 的 "
        "Table 12.2 给的是 ML²/12 这一类），与本条的**面积**二次矩（m⁴）"
        "不是一回事 —— 名字像、量纲都不同，拿前者给后者当出处是错的。"
        "**注意 `bh³/12` 与 `ML²/12` 长得很像，这正是最容易误引的地方。**",
        "七本全文检索 'second moment'（0）、'bh3' / 'bh^3'（0）、"
        "'moment of inertia'（只命中质量惯性矩：knight-physics §12.4 p.379-385、"
        "pbm-animation 节19/节21 刚体转动、engineering-math p.340 细丝绕轴的线积分）；"
        "面积二次矩属材料力学，Knight 定向 OCR 的四节里没有",
        ("statics.column_slenderness",)),

    "mechanics.radius_of_gyration": _uncited(
        "mechanics.radius_of_gyration", "回转半径",
        "i = √(I/A)；矩形截面 i = b/√12",
        "I：惯性矩（m⁴）；A：截面面积（m²）；i：回转半径（m）",
        "均质截面。依赖 `mechanics.second_moment_rectangle`，那条也 UNCITED，"
        "所以这条连推导都补不上（i=√(I/A) 里的 I 必须是**面积**二次矩，"
        "拿 Knight 的质量惯性矩代进去量纲就错了）。",
        "七本全文检索 'radius of gyration'（0 命中）、'gyration'（0，"
        "含 knight-physics 已 OCR 的 §6.1/§12.4/§12.8/§15.6 四节）；属材料力学",
        ("statics.column_slenderness",)),

    "mechanics.slenderness_ratio": _uncited(
        "mechanics.slenderness_ratio", "长细比",
        "λ = l₀ / i",
        "l₀：计算长度（m）；i：回转半径（m）；λ：无量纲",
        "受压构件；l₀ 取决于端部约束。λ 的**容许值**是规范题（GB 50010/50017），"
        "见 `codes.py`；这里缺的是 λ 的**定义**本身。",
        "七本全文检索 'slenderness'（0 命中）、'slender'（0）、'effective length'（0），"
        "knight-physics 已 OCR 的四节内同样 0；属结构力学与规范",
        ("statics.column_slenderness",)),

    "mechanics.euler_buckling": _uncited(
        "mechanics.euler_buckling", "欧拉临界力",
        "P_cr = π²·E·I / (K·L)²",
        "E：弹性模量（Pa）；I：惯性矩（m⁴）；K：计算长度系数；L：长度（m）",
        "细长杆；弹性范围；理想直杆无初始缺陷。",
        "七本全文检索 'buckling' / 'buckl'（只命中 Buckley-Leverett 方程与人名 Buckley）、"
        "'critical load'（0）、'Euler critical'（0）；engineering-math 的 Euler 条目"
        "都是 Euler 法数值积分与 Euler 方程，与压杆稳定无关。"
        "knight-physics §15.6 Elasticity（p.497-502）只讲到拉压的 Young's modulus 与"
        "应力应变，**没有稳定问题**；压杆稳定不在定向 OCR 的四节里",
        ()),

    "mechanics.simply_supported_udl_moment": _uncited(
        "mechanics.simply_supported_udl_moment", "简支梁均布荷载跨中弯矩",
        "M_max = q·L²/8",
        "q：均布线荷载（N/m）；L：跨度（m）；M：弯矩（N·m）",
        "简支；均布；小变形。**最接近的一处只是个名字**：engineering-math p.419 的"
        "著名 PDE 清单里列了 'Beam equation'，给的是动力学形式 u_tt + u_xxxx = 0，"
        "既不是静力弯矩也没有边界条件。",
        "七本全文检索 'beam'（engineering-math 命中的是 X 射线束 / 屋顶横梁的碳十四例题 /"
        " PDE 清单里的梁方程名，strang-la 命中的是显示器电子束，knight-physics p.190 "
        "命中的是一句话里的『beam will rotate about the pivot』）、"
        "'bending moment'（0）、'simply supported'（0）、'uniformly distributed load'（0，"
        "knight 的 'uniformly distributed' 说的是易拉罐里质量分布均匀）；"
        "属材料力学，不在这批书里",
        ("statics.beam_span_depth",)),

    "mechanics.tributary_area_load": _uncited(
        "mechanics.tributary_area_load", "从属面积法估柱轴力",
        "N = A_trib · (g + q) · n",
        "A_trib：从属面积（m²）；g,q：面荷载（kN/m²）；n：其上楼层数；N：轴力（kN）",
        "竖向传力；不计连续梁的内力重分布、不计偏心。"
        "这是**结构设计的工程惯例**，不是数学定理 —— 通用数理教材里本来就不会有；"
        "要取证应去荷载规范（GB 55001/GB 50009）与混凝土结构设计教材，"
        "面荷载取值那一半已在 `codes.py` 里按规范登记。",
        "七本全文检索 'tributary'（0 命中）、'load path'（0）、'axial load'（0）；"
        "属结构设计惯例而非数理定理，Knight 定向 OCR 的四节里自然也没有",
        ("statics.column_axial_ratio",)),

    # ── 数值方法（配准链路，非合理性判据，但同样需要出处）──────────
    "numerical.least_squares": _cited(
        "numerical.least_squares", "最小二乘法",
        "min‖Ax − b‖² ⇔ AᵀA x̂ = Aᵀb",
        "A：设计矩阵；b：观测；x̂：估计",
        "**AᵀA 可逆的充要条件书上有明文**（strang-la p.127）："
        "「When is AT A invertible? Answer: A must have independent columns.」，"
        "即 A 列满秩。另需误差同方差、无粗差 —— 粗差下最小二乘会被拉偏，"
        "这一点 multiple-view-geometry p.134 图 4.7 说得直白："
        "「A least-squares (orthogonal regression) fit to the point data is "
        "severely affected by the outliers」，也正是下一条 RANSAC 的理由。",
        "strang-la p.232",
        "The partial derivatives of II Ax - bll2 are zero when AT Ax = A Tb.",
        ("services.drawing_anchor",)),

    "numerical.ransac": _cited(
        "numerical.ransac", "随机抽样一致（RANSAC）",
        "重复：随机取最小样本集 → 拟合 → 数内点；取内点最多者",
        "s：实例化模型所需的最小样本数；t：内点距离阈值；T：内点数阈值；N：迭代次数",
        "**三个阈值书上都给了取法**（同书 p.137）：t 按内点距离的 χ² 分布定"
        "（α=0.95 时线模型 t²=3.84σ²、单应 t²=5.99σ²）；"
        "迭代次数 N = log(1−p)/log(1−(1−ε)ˢ)，p 通常取 0.99，ε 为粗差率 —— "
        "**本项目实测 19% 粗差**，按此表 s=2 时 N≈5、s=3 时 N≈7。"
        "前提是粗差点不构成第二个自洽模型（否则最大一致集可能选中错的那个）。",
        "multiple-view-geometry p.136",
        "(i) Randomly select a sample ofsdata points fromSand instantiate the model "
        "from this subset,\n"
        "(ii) Determine the set of data pointsSiwhich are within a distance "
        "thresholdtof the model.\n"
        "(v) AfterNtrials the largest consensus setSiis selected, and the model is\n"
        "re-estimated using all the points in the subsetSi.\n"
        "Algorithm 4.4.The RANSAC robust estimation algorithm, adapted from "
        "[Fischler-81].",
        ("services.axis_world_anchors",)),
}
