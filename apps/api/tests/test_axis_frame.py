"""Phase K-1：工程自有坐标系（轴网帧）。

**立项依据**（`docs/PHASE_K_BLUEPRINT.md`）：

| | 大歌剧院 | 轨道交通 |
|---|---|---|
| 有轴号且双向各 ≥2 条的图 | 2183 / 2309（**94.5%**） | 747 / 1707（**43.8%**） |
| 能定出坐标变换的图 | 727（31%） | 80（**4.5%**） |
| 有世界坐标的图 | 11（**0.5%**） | 0 |

**原料远比产出多。** 世界坐标近乎没有而轴号几乎每张都有——
这不是数据质量问题，是施工图的固有属性（国标不要求每张图标测量坐标，
而定位轴线是每张平面图的必备要素，GB/T 50001 §8）。

轴网帧把「轴号 → 帧内米坐标」定下来，让每张图有共同的参照物，
**全程不需要一个测量坐标**。
"""
import pytest


@pytest.mark.unit
def test_frame_from_two_drawings_that_share_labels():
    """两张图共享轴号 → 能对齐到同一帧。

    每张图各有自己的页面原点，所以**位置不能直接比**——
    要比的是「轴号之间的相对距离」。
    """
    from services.axis_frame import build_axis_frame

    frame = build_axis_frame({
        "d1": {"x": {"1": 0.0, "2": 8.0, "3": 16.0}, "y": {"A": 0.0, "B": 6.0}},
        # 同一栋楼，页面原点偏了 100 米
        "d2": {"x": {"2": 108.0, "3": 116.0}, "y": {"A": 100.0, "B": 106.0}},
    })
    assert frame.axes["x"]["1"] == pytest.approx(0.0)
    assert frame.axes["x"]["3"] == pytest.approx(16.0)
    assert frame.axes["y"]["B"] == pytest.approx(6.0)
    assert set(frame.members) == {"d1", "d2"}


@pytest.mark.unit
def test_origin_is_the_lowest_label_not_an_arbitrary_drawing():
    """**帧原点必须可复现**：取每个方向编号最小的轴（1 / A），
    而不是「第一张图的第一条轴」——后者随输入顺序变，
    同一个工程两次建帧会得到两套坐标。
    """
    from services.axis_frame import build_axis_frame

    a = build_axis_frame({"d1": {"x": {"2": 5.0, "1": 0.0}, "y": {"B": 3.0, "A": 0.0}}})
    b = build_axis_frame({"d1": {"x": {"1": 100.0, "2": 105.0},
                                 "y": {"A": 50.0, "B": 53.0}}})
    assert a.axes["x"]["1"] == pytest.approx(0.0)
    assert b.axes["x"]["1"] == pytest.approx(0.0)
    assert a.axes["x"]["2"] == b.axes["x"]["2"] == pytest.approx(5.0)


@pytest.mark.unit
def test_single_direction_drawings_do_not_join_the_frame():
    """只有单向轴号的图（剖面、立面）**不能定帧**——
    §8.28 已证方向判断通用而分区能力不通用，这里同理：
    没有两个方向就没有平面定位。
    """
    from services.axis_frame import build_axis_frame

    frame = build_axis_frame({
        "plan": {"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0, "B": 6.0}},
        "section": {"x": {"1": 0.0, "2": 8.0}, "y": {}},
    })
    assert frame.members == ["plan"]
    assert "section" in frame.rejected


@pytest.mark.unit
def test_inconsistent_drawing_is_rejected_not_averaged():
    """**同名轴号位置对不上的图要剔除，不能平均进去**——
    平均会把错误摊到所有轴上，让整帧都偏一点，
    而那种偏差事后查不出来源。
    """
    from services.axis_frame import build_axis_frame

    frame = build_axis_frame({
        "d1": {"x": {"1": 0.0, "2": 8.0, "3": 16.0}, "y": {"A": 0.0, "B": 6.0}},
        "d2": {"x": {"1": 0.0, "2": 8.0, "3": 16.0}, "y": {"A": 0.0, "B": 6.0}},
        # 轴距完全不同 —— 多半是别的单体或比例算错
        "bad": {"x": {"1": 0.0, "2": 30.0, "3": 60.0}, "y": {"A": 0.0, "B": 20.0}},
    })
    assert "bad" in frame.rejected
    assert frame.axes["x"]["2"] == pytest.approx(8.0)


@pytest.mark.unit
def test_frame_reports_per_drawing_residual():
    """每张图的对齐残差要留下来——**它是「这张图能不能信」的唯一依据**，
    下游摆放构件时要按它决定信到什么程度。
    """
    from services.axis_frame import build_axis_frame

    frame = build_axis_frame({
        "d1": {"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0, "B": 6.0}},
        "d2": {"x": {"1": 0.02, "2": 8.03}, "y": {"A": 0.0, "B": 6.01}},
    })
    assert frame.residuals["d1"] == pytest.approx(0.0, abs=0.02)
    assert 0 <= frame.residuals["d2"] < 0.1


@pytest.mark.unit
def test_empty_and_degenerate_inputs_are_safe():
    from services.axis_frame import build_axis_frame

    assert build_axis_frame({}).members == []
    assert build_axis_frame(None).axes == {"x": {}, "y": {}}
    assert build_axis_frame({"d": {"x": {"1": 0.0}, "y": {"A": 0.0}}}).members == ["d"]


# ── 多帧聚类 ──────────────────────────────────────────────────

@pytest.mark.unit
def test_incompatible_grids_become_separate_frames():
    """**一个分组里可能本就有多套轴网**，要聚类而不是强行合一。

    实测依据：按楼层+单体分组后，残差 **P25 = 0.007 米（7 毫米）**
    而中位 2.8 米——四分之一的图对齐到毫米级，方法本身没问题；
    排除法已否掉图种、专业、重复轴号、比例、方向五个假设，
    剩下的解释就是**分组里混着互不相容的轴网**。

    强行合一的代价：多数图被判「残差过大」而整批丢弃，
    而它们其实各自内部是自洽的。
    """
    from services.axis_frame import build_axis_frames

    frames = build_axis_frames({
        # 一套：轴距 8 米
        "a1": {"x": {"1": 0.0, "2": 8.0, "3": 16.0}, "y": {"A": 0.0, "B": 6.0}},
        "a2": {"x": {"1": 50.0, "2": 58.0, "3": 66.0}, "y": {"A": 20.0, "B": 26.0}},
        # 另一套：同样的轴号，轴距 30 米 —— 不是同一片轴网
        "b1": {"x": {"1": 0.0, "2": 30.0, "3": 60.0}, "y": {"A": 0.0, "B": 20.0}},
        "b2": {"x": {"1": 5.0, "2": 35.0, "3": 65.0}, "y": {"A": 3.0, "B": 23.0}},
    })
    assert len(frames) == 2
    groups = sorted(sorted(f.members) for f in frames)
    assert groups == [["a1", "a2"], ["b1", "b2"]]


@pytest.mark.unit
def test_frames_are_ordered_by_membership():
    """主轴网（成员最多的那套）排第一——下游默认取它。"""
    from services.axis_frame import build_axis_frames

    frames = build_axis_frames({
        "a1": {"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0, "B": 6.0}},
        "a2": {"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0, "B": 6.0}},
        "a3": {"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0, "B": 6.0}},
        "b1": {"x": {"1": 0.0, "2": 40.0}, "y": {"A": 0.0, "B": 30.0}},
    })
    assert len(frames[0].members) >= len(frames[-1].members)
    assert frames[0].members == ["a1", "a2", "a3"]


@pytest.mark.unit
def test_single_consistent_group_yields_one_frame():
    """本来就自洽的分组不该被拆开。"""
    from services.axis_frame import build_axis_frames

    frames = build_axis_frames({
        "d1": {"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0, "B": 6.0}},
        "d2": {"x": {"1": 10.0, "2": 18.0}, "y": {"A": 5.0, "B": 11.0}},
    })
    assert len(frames) == 1 and sorted(frames[0].members) == ["d1", "d2"]


@pytest.mark.unit
def test_empty_input_yields_no_frames():
    from services.axis_frame import build_axis_frames

    assert build_axis_frames({}) == []
    assert build_axis_frames(None) == []


@pytest.mark.unit
def test_seed_follows_the_majority_spacing_not_the_alphabet():
    """**种子不该由字母序决定**：三张图标签数相同时，
    实测按字母序选中了那张轴距 30 米的错图，
    帧围绕错图形成、把两张正确的图判成离群。

    改用多数派轴距选种子——正确的图总是多数，这是可测的事实，
    而字母序不携带任何信息。
    """
    from services.axis_frame import build_axis_frame

    frame = build_axis_frame({
        "bad": {"x": {"1": 0.0, "2": 30.0, "3": 60.0}, "y": {"A": 0.0, "B": 20.0}},
        "d1": {"x": {"1": 0.0, "2": 8.0, "3": 16.0}, "y": {"A": 0.0, "B": 6.0}},
        "d2": {"x": {"1": 0.0, "2": 8.0, "3": 16.0}, "y": {"A": 0.0, "B": 6.0}},
    })
    assert sorted(frame.members) == ["d1", "d2"]
    assert frame.axes["x"]["2"] == pytest.approx(8.0)
