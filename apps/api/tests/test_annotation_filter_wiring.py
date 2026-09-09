"""图面标注闸与识别器内建判据的关系（N1-① 的实际结论）。

**原计划**是把工作包 C 的 `annotation_filter` 接进 `element_recognizer`，
让那 13% 的删除量到达用户。动手后发现**接不上去，因为它已经被覆盖了**：

C 交付时，识别器量的是轴对齐包围盒（一条 45° 细条的影子是方的，所以细条
混得进柱窗口），C 于是写了一道用真实范围判断的过滤器。而在 C 之后，
`true_extent` 那次改造用**更根本的办法**解决了同一问题 —— 不是加过滤器，
而是把识别器的量法本身换成最小面积外接矩形。

于是 `thin_stroke` 与 `_is_plausible_column` 成了同一条判据：同样的量法、
同样的两个阈值（C 的阈值本就是回指识别器取的）。本文件把这个**恒等关系**
钉死，免得日后有人再花一轮去接一道恒等于 0 的闸。

`stroke_cluster` 是另一回事 —— 它量的东西识别器没量，见文件末尾。
"""
from __future__ import annotations

import math

import pytest

from core.model3d import annotation_filter as af
from core.model3d.element_recognizer import (
    _COLUMN_ABSURD_MIN_M,
    _COLUMN_LAYER_MAX_ASPECT,
    _COLUMN_SIZE,
    MAX_COLUMN_OUTLINE_POINTS,
    _is_column_size,
    _is_plausible_column,
)


@pytest.mark.unit
def test_thin_stroke_thresholds_are_the_recognizer_s_own():
    """两个阈值不是新数 —— 它们就是识别器里的那两个。

    这条一旦红了，说明有人改了其中一处而没改另一处，恒等关系随之破裂，
    下面那条覆盖性断言的前提也就没了。
    """
    assert af.DEFAULT_MIN_SHORT_M == _COLUMN_ABSURD_MIN_M
    assert af.DEFAULT_MIN_THIN_ASPECT == _COLUMN_LAYER_MAX_ASPECT


@pytest.mark.unit
def test_thin_stroke_is_subsumed_by_the_recognizer_gates():
    """凡被 `thin_stroke` 判为标注的，识别器两条路径都**已经**拒绝了。

    推理：`thin_stroke` 要求 `short < 0.10`；图层路径要求 `short ≥ 0.1`、
    猜测路径要求 `short ≥ 0.2`。所以是恒等关系，与图纸无关 ——
    在识别器输出上再挂一道这样的闸，额外删除量恒为 0。

    这里用一组覆盖各角度、各长宽比的构造把它跑出来，而不是只讲道理。
    """
    checked = 0
    for angle_deg in (0, 15, 30, 45, 60, 75):
        for long_m, short_m in ((2.0, 0.04), (1.4, 0.05), (0.9, 0.02),
                                (3.0, 0.09), (0.6, 0.05)):
            c = math.cos(math.radians(angle_deg))
            s = math.sin(math.radians(angle_deg))
            base = [(-long_m / 2, -short_m / 2), (long_m / 2, -short_m / 2),
                    (long_m / 2, short_m / 2), (-long_m / 2, short_m / 2)]
            poly = [[x * c - y * s, x * s + y * c] for x, y in base]

            flag = af.find_annotation_flags([{"outline": poly}])[0]
            if flag is None or flag.reason != "thin_stroke":
                continue
            checked += 1
            # 识别器拿到的是同一个真实尺寸（它也走 min_area_rect）
            w_m, h_m = af.min_area_rect(poly)
            assert not _is_plausible_column(w_m, h_m), (
                f"{long_m}×{short_m}m @{angle_deg}° 被 thin_stroke 标了，"
                "但图层路径没拒绝它 —— 恒等关系不成立了")
            assert not _is_column_size(w_m, h_m), (
                f"{long_m}×{short_m}m @{angle_deg}° 猜测路径没拒绝它")
    assert checked >= 20, f"构造里只有 {checked} 个触发了 thin_stroke，样本太少"


@pytest.mark.unit
def test_real_column_passes_both():
    """反向对照：真柱两边都不拦 —— 上面那条不是因为「什么都拒绝」而成立的。"""
    for w, h in ((0.6, 0.5), (0.4, 0.4), (1.2, 0.8)):
        poly = [[0, 0], [w, 0], [w, h], [0, h]]
        assert af.find_annotation_flags([{"outline": poly}])[0] is None
        assert _is_plausible_column(w, h)


@pytest.mark.unit
def test_stroke_cluster_measures_something_the_recognizer_does_not():
    """`stroke_cluster` **没有**被覆盖 —— 它量的对象与识别器的顶点数闸不同。

    识别器的 `_is_component_outline` 数**原始点数**（≤48）；
    `stroke_cluster` 数去重共线后的**真实角点**（>24）。真实角点 ≤ 原始点数，
    所以存在「原始点数 40 通过、真实角点 30 该拦」的中间地带。

    C 的模块文档正是栽在这个区别上的另一面：早先「三顶点多边形占 0%」
    量的是原始点数 —— 填充三角形由三条线段画成、端点两两重复恒为 6 点，
    那个 0% 是编码方式的必然。**一个「0%」先要问它量的是什么。**

    它当前接不上：构件字典里的轮廓已被 `_downsample_ring` 压到 8 点，
    角点判据在那上面恒不成立（实测 0/2497）。要启用得让 `_find_columns`
    一并带出未降点的多边形。
    """
    assert af.DEFAULT_MAX_CORNERS < MAX_COLUMN_OUTLINE_POINTS, (
        "两个数管的不是同一件事：24 是真实角点上限，48 是原始点数上限")

    # 一个真实角点很多、但原始点数仍在 48 以内的锯齿环
    zig = []
    for i in range(30):
        zig.append([i * 0.05, 0.0 if i % 2 == 0 else 0.35])
    zig.append([1.5, 0.6]); zig.append([0.0, 0.6])
    assert len(zig) <= MAX_COLUMN_OUTLINE_POINTS, "前提：识别器的原始点数闸放它过"
    assert len(af.corner_points(zig)) > af.DEFAULT_MAX_CORNERS, (
        "前提：真实角点数超过 stroke_cluster 的上限")

    flag = af.find_annotation_flags([{"outline": zig}], raw_outlines=[zig])[0]
    assert flag is not None and flag.reason == "stroke_cluster"


@pytest.mark.unit
def test_downsampled_outline_never_triggers_stroke_cluster():
    """降点后的轮廓（8 点）上，角点判据恒不成立 —— 所以「不传就不判」。"""
    ring = [[0, 0], [1, 0], [1, 0.5], [0.6, 0.8], [0, 0.8], [-0.2, 0.4], [0, 0.1], [0, 0]]
    assert len(ring) <= 8
    assert af.find_annotation_flags([{"outline": ring}], raw_outlines=[ring])[0] is None
