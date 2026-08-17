"""轴号推导单测。

**为什么是推导而不是识别**:圈内轴号是**一根一根线段画出来的字形**(CAD 出图特色),
不是文字。实测 RapidOCR 在 8 种配置(300/600/900dpi × 含圈/圈内 × 加粗 0/5)下
最好只有 **1/24** 完全命中——OCR 模型没见过发丝笔画字形。

但 GB/T 50001 §8.0.3 规定了**编写顺序**:
「横向编号应用阿拉伯数字,**从左至右**顺序编写;竖向编号应用大写拉丁字母,
**从下至上**顺序编写」。带内成员已按位置排好,**轴号因此是可推导的**。

**实测验证的统一规律**——轴号递增 ⇔ 法向偏移递减,四个带(含两个旋转带)一致:

    分区1 数字 1-1→1-24   偏移 -992 → -2522
    分区2 字母 2-A→2-P    偏移 1529 → 778
    分区3 数字 3-1→3-16   偏移 -1653 → -2530   (132° 旋转带)
    分区3 字母 3-A→3-Q    偏移 -434 → -1182    (42° 旋转带)

OCR 仍有用——不用来读全名,只用来**锚定序列起点**。
"""
import pytest

from core.model3d.axis_label_derive import (
    ALPHA_KIND, NUMERIC_KIND, anchor_from_reads, derive_band_labels,
    label_kind_for_axis_angle, order_axes_for_labelling,
)


def _ax(offset: float, angle: float = 90.0) -> dict:
    return {"offset_pt": offset, "angle_deg": angle}


# ── 数字 / 字母的归属(§8.0.3)────────────────────────────────────

def test_vertical_axes_get_numbers():
    """§8.0.3 横向编号用数字 —— 标注的是**竖向**轴线。"""
    assert label_kind_for_axis_angle(90.0) == NUMERIC_KIND


def test_horizontal_axes_get_letters():
    assert label_kind_for_axis_angle(0.0) == ALPHA_KIND
    assert label_kind_for_axis_angle(180.0) == ALPHA_KIND


def test_rotated_axes_follow_the_nearer_orthogonal_direction():
    """旋转分区实测:132° 轴线编数字、42° 轴线编字母 —— 与「靠近哪个正交方向」一致。"""
    assert label_kind_for_axis_angle(132.0) == NUMERIC_KIND   # 偏竖向
    assert label_kind_for_axis_angle(42.0) == ALPHA_KIND      # 偏横向


def test_exactly_45_degrees_is_resolved_deterministically():
    """45° 等距时必须给出稳定结果,不能随浮点抖动翻转。"""
    assert label_kind_for_axis_angle(45.0) == label_kind_for_axis_angle(45.0)


# ── 排序(轴号递增 ⇔ 偏移递减)──────────────────────────────────

def test_axes_are_ordered_by_descending_offset():
    axes = [_ax(-2522.0), _ax(-992.0), _ax(-1500.0)]
    got = [a["offset_pt"] for a in order_axes_for_labelling(axes)]
    assert got == [-992.0, -1500.0, -2522.0]


def test_ordering_does_not_mutate_input():
    axes = [_ax(-2522.0), _ax(-992.0)]
    order_axes_for_labelling(axes)
    assert axes[0]["offset_pt"] == -2522.0


def test_ordering_matches_real_letter_band():
    """分区 2 字母带:2-A 在 y=1529(下),2-P 在 y=778(上)。"""
    axes = [_ax(778.0, 0.0), _ax(1529.0, 0.0), _ax(1100.0, 0.0)]
    got = [a["offset_pt"] for a in order_axes_for_labelling(axes)]
    assert got[0] == 1529.0 and got[-1] == 778.0


# ── 推导 ──────────────────────────────────────────────────────

def test_derive_numeric_sequence_for_zone1_bottom_row():
    axes = [_ax(-992.0 - i * 65.0) for i in range(24)]
    got = derive_band_labels(axes, zone="1")
    assert got[0]["label"] == "1-1" and got[-1]["label"] == "1-24"
    assert len(got) == 24


def test_derive_letter_sequence_skips_forbidden_letters():
    """§8.0.4:I、O、Z 不得用作轴线编号。14 条 → A..N 跳过 I。"""
    axes = [_ax(1529.0 - i * 55.0, 0.0) for i in range(14)]
    labels = [a["label"] for a in derive_band_labels(axes, zone="2")]
    assert labels[:9] == ["2-A", "2-B", "2-C", "2-D", "2-E", "2-F", "2-G", "2-H", "2-J"]
    assert labels[-1] == "2-P"
    assert "2-I" not in labels and "2-O" not in labels


def test_derive_rotated_zone3_matches_ground_truth():
    """分区 3:132° 轴线 16 条 → 3-1..3-16;42° 轴线 15 条 → 3-A..3-Q。"""
    num = derive_band_labels([_ax(-1653.0 - i * 58.0, 132.0) for i in range(16)],
                             zone="3")
    alpha = derive_band_labels([_ax(-434.0 - i * 53.0, 42.0) for i in range(15)],
                               zone="3")
    assert [a["label"] for a in num][-1] == "3-16"
    assert [a["label"] for a in alpha][-1] == "3-Q"


def test_derive_without_zone_omits_prefix():
    """分区号无法从几何推出;没有它就不加前缀,不能瞎猜一个。"""
    labels = [a["label"] for a in derive_band_labels([_ax(-100.0)], zone=None)]
    assert labels == ["1"]


def test_derive_honours_explicit_start():
    """带可能不从 1 开始(如只标注了一段)。"""
    axes = [_ax(-100.0 - i * 60.0) for i in range(3)]
    labels = [a["label"] for a in derive_band_labels(axes, zone="1", start="5")]
    assert labels == ["1-5", "1-6", "1-7"]


def test_derive_marks_source_as_derived_not_recognized():
    """来源必须诚实:这是**推导**出来的,不是识别出来的。"""
    got = derive_band_labels([_ax(-100.0)], zone="1")
    assert got[0]["label_source"] == "derived"


def test_derive_carries_axis_fields_through():
    got = derive_band_labels([_ax(-100.0, 90.0)], zone="1")
    assert got[0]["offset_pt"] == -100.0 and got[0]["angle_deg"] == 90.0


def test_derive_on_empty_axes():
    assert derive_band_labels([], zone="1") == []


# ── OCR 锚定(不读全名,只定起点)─────────────────────────────────

def test_anchor_confirms_a_correct_sequence():
    """实测 OCR 在 24 个圈上读出 9/10/12/13/15/16/18/20/22/23,位置全部对上。

    下标是 0-based:轴号 `1-9` 位于第 8 位。
    """
    derived = [str(i) for i in range(1, 25)]
    reads = {8: "9", 9: "10", 11: "12", 12: "13", 14: "15",
             15: "16", 17: "18", 19: "20", 21: "22", 22: "23"}
    got = anchor_from_reads(derived, reads)
    assert got["shift"] == 0
    assert got["agreements"] == 10
    assert got["conflicts"] == 0


def test_anchor_detects_a_shifted_start():
    """若带其实从 5 开始,锚定应报出位移而不是硬套 1 开头。"""
    derived = [str(i) for i in range(1, 11)]
    reads = {0: "5", 3: "8", 7: "12"}
    got = anchor_from_reads(derived, reads)
    assert got["shift"] == 4


def test_anchor_reports_conflicts_without_hiding_them():
    """实测位置 24 被读成 '2'(错读)。冲突必须报出来,不能默默丢掉。"""
    derived = [str(i) for i in range(1, 25)]
    reads = {9: "10", 23: "2"}          # 第 9 位是 1-10(读对),第 23 位是 1-24(误读)
    got = anchor_from_reads(derived, reads)
    assert got["shift"] == 0
    assert got["agreements"] == 1 and got["conflicts"] == 1


def test_anchor_with_no_reads_defaults_to_no_shift():
    got = anchor_from_reads(["1", "2", "3"], {})
    assert got["shift"] == 0 and got["agreements"] == 0
    assert got["confident"] is False


def test_anchor_confidence_requires_multiple_agreements():
    """单个吻合可能是巧合;要多点一致才敢说锚定成功。"""
    weak = anchor_from_reads([str(i) for i in range(1, 25)], {9: "10"})
    strong = anchor_from_reads([str(i) for i in range(1, 25)],
                               {9: "10", 11: "12", 14: "15"})
    assert weak["confident"] is False
    assert strong["confident"] is True


def test_anchor_ignores_reads_out_of_range():
    got = anchor_from_reads(["1", "2"], {99: "100"})
    assert got["agreements"] == 0


def test_anchor_normalizes_whitespace_and_case():
    got = anchor_from_reads(["A", "B", "C"], {1: " b "})
    assert got["agreements"] == 1


def test_anchor_prefers_the_shift_with_fewest_conflicts_on_a_tie():
    """吻合数相同时选冲突更少的,避免任选一个造成不稳定输出。"""
    got = anchor_from_reads([str(i) for i in range(1, 6)], {0: "1", 1: "9"})
    assert got["shift"] == 0
    assert got["conflicts"] == 1


def test_anchor_does_not_win_by_pushing_reads_out_of_range():
    """位移把某位置推成非法轴号(0/负数)时算冲突,不能当免票。

    否则 shift=-22 会因为「让读不对的位置消失」而胜出——实测踩过。
    """
    derived = [str(i) for i in range(1, 25)]
    got = anchor_from_reads(derived, {9: "10", 23: "2"})
    assert got["shift"] == 0
    assert abs(got["shift"]) < 5


# ── 分区级推导(必须按方向分开)────────────────────────────────────

def test_zone_labels_split_numeric_and_alpha_by_direction():
    """一个分区同时含数字向与字母向轴线,混在一起推导会让整批轴号错位。

    实测踩过:39 条轴线用同一种类型推导,24 个数字标签全错。
    """
    from core.model3d.axis_label_derive import derive_zone_labels

    axes = ([_ax(-992.0 - i * 65.0, 90.0) for i in range(24)]
            + [_ax(2137.0 - i * 34.0, 0.0) for i in range(15)])
    got = derive_zone_labels(axes, zone="1")
    labels = {a["label"] for a in got}
    assert len(got) == 39
    assert {"1-1", "1-24"} <= labels          # 数字向完整
    assert {"1-A", "1-Q"} <= labels           # 字母向完整
    assert "1-I" not in labels and "1-O" not in labels


def test_zone_labels_accept_per_kind_starts():
    from core.model3d.axis_label_derive import derive_zone_labels

    axes = [_ax(-100.0, 90.0), _ax(500.0, 0.0)]
    labels = {a["label"] for a in derive_zone_labels(
        axes, zone="2", starts={"numeric": "5", "alpha": "C"})}
    assert labels == {"2-5", "2-C"}


def test_zone_labels_on_empty():
    from core.model3d.axis_label_derive import derive_zone_labels
    assert derive_zone_labels([], zone="1") == []
