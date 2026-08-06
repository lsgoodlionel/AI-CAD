"""真值基准自检:人工计数必须内部一致,否则召回率的分母就是错的。"""
import pytest

from tests.fixtures.axis_ground_truth import (
    A_01_02A, A_01_03A, A_01_04A, BASELINE_RECALL, CIRCLE_ANCHOR_RESULT, DENOMINATOR,
    CHAR_COUNT_CRITERION, COORD_ANCHOR_RESULT, FRACTION_RATIO_MEASURED,
    LABEL_DERIVE_RESULT, LABEL_DERIVE_V5, LABEL_DERIVE_V6,
    LEAST_SQUARES_FAILURE, SPACING_CRITERION_FAILS,
    OCR_LABEL_BASELINE, REFLECTION_REQUIRED, WORLD_PLACEMENT_RESULT,
    horizontal_spacing_consistency, label_circle_budget, spacing_consistency,
    total_axis_count, zone_axis_count,
)

FORBIDDEN_LETTERS = {"I", "O", "Z"}          # GB/T 50001 §8.0.4
ALL_ZONES = ("1", "2", "3")


# ── 计数一致性(两重独立交叉校验) ────────────────────────────────

def test_every_zone_spacing_count_matches_axis_count():
    """轴距条数 = 覆盖轴线数 - 1。不成立说明我读图时数漏了。"""
    for zone in ALL_ZONES:
        assert spacing_consistency(A_01_02A, zone), f"分区 {zone} 竖向轴距条数不符"
        assert horizontal_spacing_consistency(A_01_02A, zone), f"分区 {zone} 横向不符"


def test_label_circle_count_equals_main_circles_plus_additional():
    """圈数 = 主轴线**圈数** + 附加轴线数 —— 独立于尺寸链的第二重校验。

    108 = 100 主圈 + 8 附加。**此前写成「99 主轴线 + 9 附加」是错的**:
    它假设一轴一圈,而 §8.0.2 允许两端各注一个(见
    `test_circle_count_identity_accounts_for_two_ended_axes`)。
    """
    assert label_circle_budget(A_01_02A) == A_01_02A["additional_axis_count"]


# ── 国标合规(真值自身也要守标准) ────────────────────────────────

def test_all_zones_letter_labels_skip_forbidden_letters():
    """§8.0.4:I、O、Z 不得用作轴线编号。三个分区都要守。"""
    for zone in ALL_ZONES:
        letters = {lb.split("-")[1]
                   for lb in A_01_02A["zones"][zone]["horizontal_labels"]}
        assert not (FORBIDDEN_LETTERS & letters), f"分区 {zone} 用了禁用字母"


def test_labels_carry_zone_prefix():
    """§8.0.5 分区编号:每个轴号都是「分区号-轴线号」。"""
    for zone in ALL_ZONES:
        z = A_01_02A["zones"][zone]
        for label in z["vertical_labels"] + z["horizontal_labels"]:
            assert label.startswith(f"{zone}-"), f"{label} 缺分区前缀"


# ── 分区计数 ──────────────────────────────────────────────────

def test_zone_counts():
    assert zone_axis_count(A_01_02A, "1") == 24 + 15
    assert zone_axis_count(A_01_02A, "2") == 15 + 14
    assert zone_axis_count(A_01_02A, "3") == 16 + 15


def test_rotated_zone_is_a_real_zone_not_bracing():
    """分区 3 曾被误判成「斜撑构件」。轴号自成 3-* 序列即证明它是分区。"""
    z = A_01_02A["zones"]["3"]
    assert len(z["vertical_labels"]) == 16 and len(z["horizontal_labels"]) == 15
    assert all(lb.startswith("3-") for lb in z["vertical_labels"])


def test_no_zone_is_marked_partial_anymore():
    """真值已补全,不应再有 partial 分区拉低分母。"""
    assert all(not z.get("partial") for z in A_01_02A["zones"].values())
    assert total_axis_count(A_01_02A) == 99


def test_denominator_matches_zone_sums():
    """按方向系归并的分母必须与按分区的和一致,否则两套口径会打架。"""
    d = DENOMINATOR["A-01-02A"]
    assert d["vertical"] == 24 + 15
    assert d["horizontal"] == 15 + 14
    assert d["rotated"] == 16 + 15
    assert d["main_total"] == d["vertical"] + d["horizontal"] + d["rotated"] == 99


# ── 图面事实留档 ──────────────────────────────────────────────

def test_corner_coordinates_recorded():
    coords = A_01_02A["corner_coordinates"]
    assert len(coords) == 6
    # 实测坐标有正有负,不能假设符号
    assert any(c["y"] > 0 for c in coords) and any(c["y"] < 0 for c in coords)


def test_polar_grid_is_not_center_radial():
    """放射线沿最内圈切线,不过圆心——按「过心直线」去找会全错。"""
    assert A_01_03A["polar"]["radial_from_center"] is False
    assert A_01_03A["polar"]["full_circles"] == 7


def test_label_circle_diameter_differs_across_drawings():
    """圈径按图内众数判定,不能硬编码全局常量:实测 28.0 vs 16.0pt。"""
    assert A_01_02A["label_circles"]["diameter_pt"] == 28.0
    assert A_01_04A["label_circles"]["diameter_pt"] == 16.0
    assert A_01_02A["label_circles"]["arcs_per_circle"] == 4


def test_a0102_circle_diameter_within_national_standard():
    """§8.0.2:轴号圆直径 8~10mm。28.0pt = 9.88mm,合规。"""
    mm = A_01_02A["label_circles"]["diameter_pt"] / 72.0 * 25.4
    assert 8.0 <= mm <= 10.0


# ── 基线留档 ──────────────────────────────────────────────────

def test_baseline_recall_records_denominator_alongside_number():
    """**百分比必须连同分母一起读**:v2 的 92% 用的是不完整的分母 52。"""
    v1 = BASELINE_RECALL["A-01-02A@v1"]["total"]
    v2 = BASELINE_RECALL["A-01-02A@v2"]["total"]
    assert v1 == (31, 52) and v2 == (48, 52)
    # 两版都以 52 为分母,而真实主轴线是 99 —— 旧分母系统性偏小
    assert v2[1] < DENOMINATOR["A-01-02A"]["main_total"]


def test_v2_recomputed_on_full_denominator_is_much_lower():
    """同一份检出结果换成完整分母只有 71% —— 92% 是分母造成的假象。"""
    got, total = BASELINE_RECALL["A-01-02A@v2-refair"]["total"]
    assert got / total == pytest.approx(0.71, abs=0.01)
    # 旋转系严重过检:检出 75 条,真值 31 条
    r_got, r_total = BASELINE_RECALL["A-01-02A@v2-refair"]["rotated"]
    assert r_got > r_total * 2


def test_v3_circle_anchored_beats_geometry_route_on_both_ends():
    """圈锚定路线:召回上去、过检下来 —— 两头都赢才算真的赢。"""
    v2 = BASELINE_RECALL["A-01-02A@v2-refair"]
    v3 = BASELINE_RECALL["A-01-02A@v3-circle"]
    assert v3["vertical"] == (39, 39)              # 竖向精确命中
    assert v3["rotated"] == (31, 31)               # 旋转从过检 75 → 精确 31
    assert v2["rotated"][0] > v3["rotated"][0]     # 过检被消除,而非放宽口径
    got, total = v3["total"]
    assert got / total >= 0.90                     # 达到 90% 硬门槛


def test_circle_count_identity_accounts_for_two_ended_axes():
    """圈数 = 主轴线**圈数** + 附加轴线数,**不是**主轴线**数** + 附加轴线数。

    §8.0.2 允许轴线两端各注一个编号圈,实测分区 1 就有一条如此
    (100 个主圈对应 99 条主轴线)。按错误恒等式算出的附加轴线数是 9,
    用字形判据(`/` 笔画长度)直接数是 8 —— 差的正是那条两端各一圈的轴线。
    """
    d, g = DENOMINATOR["A-01-02A"], A_01_02A
    assert d["all_total"] == g["label_circles"]["count"] == 108
    assert g["main_label_circle_count"] + g["additional_axis_count"] == 108
    # 主圈数 ≥ 主轴线数,差额就是两端各一圈的轴线条数
    assert g["main_label_circle_count"] >= d["main_total"]
    assert g["main_label_circle_count"] - d["main_total"] == 1


def test_polar_drawing_band_rate_is_expectedly_low():
    """A-01-03A 成带率低是**预期**,不是回归:同心圆+切线放射套不上直线带。"""
    r = CIRCLE_ANCHOR_RESULT["A-01-03A"]
    assert r["banded"] / r["circles"] < 0.7
    for no in ("A-01-02A", "A-01-04A"):
        rr = CIRCLE_ANCHOR_RESULT[no]
        assert rr["banded"] / rr["circles"] >= 0.98


# ── 轴号推导留档 ──────────────────────────────────────────────

def test_label_derive_crosses_the_90_percent_bar():
    """首次产出**带名字**的轴线:92/99 = 93%。"""
    got, total = LABEL_DERIVE_RESULT["A-01-02A"]["total"]
    assert got / total >= 0.90


def test_zone1_labels_are_exactly_right():
    """分区 1 的 39 条轴线逐条全中 —— 说明推导链本身没有系统偏差。"""
    detected, hit, truth = LABEL_DERIVE_RESULT["A-01-02A"]["zones"]["1"]
    assert detected == hit == truth == 39


def test_v4_hit_equals_detected_is_a_set_level_property():
    """v4 每个分区「命中数 = 检出数」——但这是**集合口径**。

    ⚠️ 不要据此说「零错标」:v5 补上小带后,分区 2 检出 18 条字母轴线而真值
    只有 14 条,4 条附加轴线夹在序列中间导致其后轴号偏移(见
    `test_label_set_coverage_is_not_per_axis_correctness`)。
    集合覆盖与逐条正确是两回事。
    """
    for detected, hit, _truth in LABEL_DERIVE_RESULT["A-01-02A"]["zones"].values():
        assert hit == detected


def test_misses_are_attributed_to_unassigned_small_bands():
    """7 条未命中全部来自 3 条未并入分区的小带,根因已验证不是推导错。"""
    zones = LABEL_DERIVE_RESULT["A-01-02A"]["zones"]
    missed = sum(truth - hit for _d, hit, truth in zones.values())
    assert missed == 7
    assert LABEL_DERIVE_RESULT["A-01-02A"]["unassigned_small_bands"] == 3


def test_ocr_baseline_justifies_the_derivation_route():
    """OCR 8 种配置最好 1/24 —— 这就是为什么走「推导 + 锚定」而不是识别。"""
    b = OCR_LABEL_BASELINE["A-01-02A"]
    assert b["best_exact"] / b["of"] < 0.10
    assert b["configs_tried"] >= 8


# ── 坐标锚点留档(M-I5 的钥匙)──────────────────────────────────

def test_world_anchors_reach_millimetre_accuracy():
    """15 个页面↔工程坐标锚点,残差 5.7 毫米 —— 图纸可精确放进真实世界。"""
    r = COORD_ANCHOR_RESULT["A-01-02A"]
    assert r["usable_anchors"] >= 3          # 相似变换只需 2 对,15 对余量充足
    assert r["rmse_m"] < 0.01


def test_every_leader_yields_a_parsed_coordinate():
    """16 处引线全部读出坐标 —— 坐标文字比轴号大一个量级,OCR 完全够用。"""
    r = COORD_ANCHOR_RESULT["A-01-02A"]
    assert r["parsed"] == r["leaders"] == 16


def test_sign_errors_need_both_repair_paths():
    """X 靠簇共识、Y 只能靠变换 —— 缺任一条都修不全。

    Y 值本来正负混杂(-179.651 ~ +47.504),共识法按设计不敢动它。
    """
    r = COORD_ANCHOR_RESULT["A-01-02A"]
    assert r["sign_fixed_by_consensus"] >= 1
    assert r["sign_fixed_by_transform"] >= 1


def test_gross_error_is_surfaced_not_swallowed():
    """OCR 误读必须标出来交给人工——错的世界坐标比缺锚点危险得多。"""
    r = COORD_ANCHOR_RESULT["A-01-02A"]
    assert r["gross_errors"] == 1
    assert r["usable_anchors"] == r["parsed"] - r["gross_errors"]


def test_least_squares_alone_would_have_failed():
    """留档为什么必须 RANSAC:19% 粗差下最小二乘残差无分界。"""
    ls = LEAST_SQUARES_FAILURE["A-01-02A"]
    anchor = COORD_ANCHOR_RESULT["A-01-02A"]
    assert ls["rmse_m"] > 50                              # 最小二乘 RMSE 72.5m
    assert anchor["rmse_m"] * 1000 < ls["rmse_m"]         # RANSAC 好四个数量级
    # 最大残差不到 RMSE 的 3 倍 → 常规「剔除最大残差」策略根本触发不了
    assert ls["residual_max_m"] < 3 * ls["rmse_m"]


# ── 小带吸附后的更正留档 ──────────────────────────────────────

def test_label_set_coverage_is_not_per_axis_correctness():
    """**集合口径 ≠ 逐条正确**。99/99 只说明标签集全覆盖。

    分区 2 字母向检出 18 条 vs 真值 14 条——4 条附加轴线夹在序列中间,
    其后的轴号整体偏移(真实 2-H 被标成 2-J)。所以**存在错标**,
    此前基于集合口径说的「零错标」必须更正。
    """
    v5 = LABEL_DERIVE_V5["A-01-02A"]
    assert v5["label_set_coverage"] == (99, 99)
    assert v5["detected_axes"]["2"][1] > v5["truth_axes"]["2"][1]
    assert "2" in v5["sequence_shift_zones"]


def test_zone1_and_zone3_have_no_sequence_shift():
    """检出数 = 真值数的分区没有偏移问题。"""
    v5 = LABEL_DERIVE_V5["A-01-02A"]
    for zone in ("1", "3"):
        assert v5["detected_axes"][zone] == v5["truth_axes"][zone]
        assert zone not in v5["sequence_shift_zones"]


def test_spacing_criterion_cannot_separate_additional_axes():
    """「间距小 = 附加轴线」在单张图内就自相矛盾,不能用。

    分区 2 的附加轴距最小 8.5pt、常规 56.8pt,看似可分;
    但分区 1 的**真实主轴距**就有 8.4pt,任何阈值都会误杀它。
    """
    g = SPACING_CRITERION_FAILS["A-01-02A"]
    zone2_additional = [x for x in g["zone2_alpha_gaps_pt"] if x < 25]
    zone1_main = g["zone1_alpha_gaps_pt"]
    assert min(zone1_main) < max(zone2_additional)      # 区间重叠 → 无法分开


# ── 字形判据(v6):偏移消除 ──────────────────────────────────────

def test_fraction_glyph_removes_every_sequence_shift():
    """排除附加轴线后,三个分区各方向的轴线条数与真值**逐项相等**。

    这不再是集合口径:条数逐项相等,序列就不可能偏移。
    """
    v6 = LABEL_DERIVE_V6["A-01-02A"]
    assert v6["sequence_shift_zones"] == []
    for zone, (num, alpha) in v6["per_zone"].items():
        truth = A_01_02A["zones"][zone]
        assert (num, alpha) == (len(truth["vertical_labels"]),
                                len(truth["horizontal_labels"]))


def test_slash_ratio_has_an_empty_band_across_all_drawings():
    """0.42(字母斜画)与 0.47+(`/`)之间三张图全空 —— 阈值不是拍的。"""
    for m in FRACTION_RATIO_MEASURED.values():
        assert m["letter_diagonals"] < 0.44


def test_polar_drawing_has_no_fraction_labels():
    """A-01-03A 是同心圆轴网,实测 0 个分数式标注 —— 判据没有乱开火。"""
    assert FRACTION_RATIO_MEASURED["A-01-03A"]["slash_count"] == 0


def test_char_count_criterion_was_measured_and_rejected():
    """字符个数只找到 6/8;漏的两个字形在 x 上接触,簇数退化为 2。

    留档是为了防止有人再回头去调那个分簇阈值——实测已证明不是阈值问题。
    """
    c = CHAR_COUNT_CRITERION["A-01-02A"]
    assert c["found"] < c["of"]
    assert c["missed_cluster_count"] == 2
    assert c["missed_slash_ratio"] > 0.44      # `/` 笔画本身完全正常


# ── M-I5:识别成果喂进建模 ────────────────────────────────────────

def test_world_placement_is_solved_and_not_suspect():
    """`placements` 此前恒为 0(无锚点、无变换);现在求解成功且残差 6.1 毫米。"""
    r = WORLD_PLACEMENT_RESULT["A-01-02A"]
    assert r["placements"] >= 1
    assert r["rmse_m"] < 0.01 and r["suspect"] is False


def test_only_ransac_inliers_become_anchors():
    """粗错不入锚点表 —— 写入数必须小于读出数。"""
    r = WORLD_PLACEMENT_RESULT["A-01-02A"]
    assert r["anchors_written"] < r["leaders"]
    assert r["gross_errors"] >= 1


def test_reflection_support_is_what_made_placement_possible():
    """工程坐标 X=北/Y=东 是左手系;不支持反射时残差 105m,图被跳过。"""
    r = REFLECTION_REQUIRED["A-01-02A"]
    assert r["rmse_without_reflection_m"] > 50
    assert r["rmse_with_reflection_m"] < 0.01


def test_every_fraction_axis_was_visually_confirmed():
    """判据的产物不能自证 —— 8 条附加轴线逐个渲图读出后才作数。

    其中 `1-1/L` 在**分区 1**,说明附加轴线不是分区 2 独有。
    """
    from tests.fixtures.axis_ground_truth import FRACTION_LABELS_CONFIRMED

    c = FRACTION_LABELS_CONFIRMED["A-01-02A"]
    assert c["visually_confirmed"] == c["detected"] == 8
    assert len(c["labels"]) == 8
    assert any(lb.startswith("1-") for lb in c["labels"])


def test_crop_window_fix_cut_gross_errors_on_every_drawing():
    """裁图窗口按引线尺度后,三张图的粗错全部大幅下降。

    固定 ±130pt 窗在引线短的图上一次框进 2~3 处标注 —— 这是 A-01-04A
    20 条引线只剩 6 条内点的主因。
    """
    from tests.fixtures.axis_ground_truth import CROP_WINDOW_FIX

    for no, m in CROP_WINDOW_FIX.items():
        before, after = m["outliers"]
        assert after <= before, no
    # 引线尺度差 2.5 倍 —— 固定窗口不可能同时适配
    lens = [m["leader_h_pt"] for m in CROP_WINDOW_FIX.values()]
    assert max(lens) / min(lens) > 2.0


def test_polar_and_vertical_drawings_improved_most():
    """短引线的两张图改善最大,与根因一致(窗口相对越大,污染越重)。"""
    from tests.fixtures.axis_ground_truth import CROP_WINDOW_FIX

    for no in ("A-01-03A", "A-01-04A"):
        before, after = CROP_WINDOW_FIX[no]["rmse_mm"]
        assert after < before / 1.5, no


def test_polar_layout_is_angular_not_linear():
    """A-01-03A 的轴号圈按**角度**等间距排布,不是直线成带。

    直线带模型只吃掉 66/107 —— 这不是回归,是模型不匹配:
    极坐标轴网里角度扮演「沿带位置」、半径扮演「法向偏移」。
    """
    p = A_01_03A["polar_layout"]
    assert p["linear_banded"] / p["circles"] < 0.7
    assert 3.0 < p["angular_step_deg"] < 6.0
    # 半径跨度极大 —— 用半径当「带」的分组键分不开
    lo, hi = p["radius_range_pt"]
    assert hi / lo > 10


def test_polar_center_confirmed_by_rendering():
    """渲图确认放射线汇聚点与中垂线投票的圆心一致(误差 <30pt)。"""
    import math
    got = A_01_03A["polar"]["center_page_pt"]
    rendered = (1668.0, 1062.0)
    assert math.dist(got, rendered) < 30.0


def test_polar_band_covers_far_more_circles_than_linear():
    """极坐标带把 A-01-03A 的覆盖从 66/107 提到 106/107。"""
    p = A_01_03A["polar_layout"]
    assert p["polar_banded"] > p["linear_banded"] * 1.5
    assert p["radial_axes"] > 60


def test_regularity_separates_polar_from_orthogonal():
    """角间距规律度是有效的**分类器**:放射 0.76,正交 0.47/0.44。"""
    r = A_01_03A["polar_layout"]["regularity"]
    assert r["polar"] > max(r["orthogonal"]) * 1.5


def test_regularity_is_not_an_accurate_locator():
    """但它不是好**定位器** —— 极大值偏真圆心 67pt(半径 900 上 4.3°,
    与 4.4° 角间距同量级,会让整圈轴号偏一位)。

    裁决依据是圆心工程坐标反算的页面位置,独立于任何几何判据。
    """
    e = A_01_03A["center_estimate_errors_pt"]
    assert e["regularity_search"] > 4 * e["bisector_vote"]
    assert e["rendered_eyeball"] < e["bisector_vote"] < e["regularity_search"]


def test_outline_falsifications_are_recorded():
    """外轮廓已证伪的假设要留档 —— 否则会有人再按「直线边」去找一遍。"""
    from tests.fixtures.axis_ground_truth import OUTLINE_FALSIFIED

    f = OUTLINE_FALSIFIED["A-01-02A"]
    assert "dash_dot" in f["corners_are_coord_tips"]["result"]
    # 「右侧有一条平滑长弧」是目视判断错误,已撤回 —— 撤回本身也要留档
    assert "目视判断错误" in f["located_visually_RETRACTED"]
    assert "located_visually" not in f


def test_outline_lives_on_its_own_drawings_not_the_axis_grid_one():
    """三个外轮廓假设的共同根因是**找错图**:轴网定位图上本就没有外轮廓。

    国标依据:GB/T 50001 §4.0.2 只定义**细线**(0.25b)用途,
    粗/中的单点·双点长画线原文写「见各有关专业制图标准」;
    用地红线属总图专业(附录 B 图层 `总图用地红线`),归 GB/T 50103 管。
    而本项目 2309 张图**没有一张总平面图**,故不存在用地红线。
    """
    from tests.fixtures.axis_ground_truth import OUTLINE_DRAWING_TRUTH as T

    assert T["project_has_site_plan"] is False
    arcs = T["arcs_by_drawing"]
    # 轴网定位图矢量层面一条弧都没有
    assert arcs["A-01-02A"]["arcs"] == 0
    # 板边控制线是折线,直边为主
    assert arcs["A-02-02A"]["arcs"] == 0
    # 扇形屋盖结构边线才有真弧,且两条圆心几乎重合 = 扇形圆心
    fan = arcs["A-02-03A"]
    assert fan["arcs"] == 3
    (_cx0, _cy0), (cx1, cy1), (cx2, cy2) = fan["centers_pt"]
    assert abs(cx1 - cx2) < 100 and abs(cy1 - cy2) < 100


def test_only_arc_fitting_reaches_label_accuracy():
    """派轴号需要误差 ≤ 约 15pt(半径 900 上 1°)。

    **这条断言此前是「没有任何方法达到」——同心弧拟合把它推翻了**(1.2pt)。
    其余三种自动方法仍然都不够,所以定心必须走弧路线。
    """
    e = A_01_03A["center_estimate_errors_pt"]
    others = {k: v for k, v in e.items()
              if k not in ("rendered_eyeball", "concentric_arc_fit")}
    assert min(others.values()) > 15.0          # 其余自动方法仍不够
    assert e["concentric_arc_fit"] < 15.0       # 弧拟合够了
    assert e["circumcenter_vote"] > e["regularity_search"] > e["bisector_vote"]


def test_arc_based_centering_finally_reaches_label_accuracy():
    """同心弧定心 1.2pt,远优于派轴号所需的 ≤15pt(半径 900 上 1°)。

    此前四种方法最好也只有 16.2pt —— 刚好不够。
    """
    e = A_01_03A["center_estimate_errors_pt"]
    assert e["concentric_arc_fit"] < 2.0
    assert e["concentric_arc_fit"] < e["bisector_vote"] / 10


def test_polar_band_now_covers_every_circle():
    """精确圆心下覆盖 107/107(直线带 66,粗圆心极坐标带 106)。"""
    r = A_01_03A["arc_center_result"]
    assert r["circles_covered"] == A_01_03A["label_circles"]["count"] == 107
    assert r["radial_axes"] > 70
    assert 4.0 < r["angular_step_deg"] < 4.6


def test_arc_pipeline_needed_both_fixes():
    """追链解决「弧被炸碎」,抽稀解决「曲率被噪声淹没」—— 缺一不可。

    实测:追出 1116 段的长链,但不抽稀时拟合出的弧半径中位只有 8pt(全是噪声)。
    """
    r = A_01_03A["arc_center_result"]
    assert r["longest_chain"] > 1000          # 追链成功(此前是 53100 段的巨链)
    assert r["arcs_over_200pt"] >= 15         # 抽稀后大半径弧才出得来
    lo, hi = r["sweep_range_deg"]
    assert lo > 200 and hi > 300              # 接近整圆,确实是同心圆
