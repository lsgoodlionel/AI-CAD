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


@pytest.mark.unit
def test_clustering_is_joint_across_both_directions():
    """**帧是二维的，不是两个独立的一维系统。**

    实测：x 与 y 分别独立聚类时，两个方向各选各的种子、各自成团，
    再要求一张图两方向都符合才进帧——于是最大的分组（399 张）
    **一帧都建不出来**，连种子自己在 y 方向的残差都有 2.99 米
    （它符合 x 的那一团，却属于 y 的另一团）。

    正确做法：候选必须**同时**在两个方向与同一个池子相容才并入。
    """
    from services.axis_frame import build_axis_frame

    frame = build_axis_frame({
        # A 组：x 轴距 8、y 轴距 6
        "a1": {"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0, "B": 6.0}},
        "a2": {"x": {"1": 20.0, "2": 28.0}, "y": {"A": 10.0, "B": 16.0}},
        # B 组：x 轴距**相同**（8），但 y 轴距 40 —— x 像、y 不像
        "b1": {"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0, "B": 40.0}},
        "b2": {"x": {"1": 20.0, "2": 28.0}, "y": {"A": 10.0, "B": 50.0}},
    })
    # 只按 x 聚类会把四张都收进来；联合聚类必须把 B 组挡在外面
    assert sorted(frame.members) == ["a1", "a2"]
    assert "b1" in frame.rejected and "b2" in frame.rejected


@pytest.mark.unit
def test_offset_is_the_total_shift_not_just_the_residual_correction():
    """**存的平移量必须是「加上去就落进帧」的总量。**

    实测教训：生长时先算了一个大的对齐平移把图挪到共识坐标系，
    最后又算了一次归零后的残余修正，而只把后者存了下来——
    大的那一半丢了。只读对照立刻暴露：轨道交通换到帧内后
    包络/核心比从 3.05 涨到 **15.74**，因为每张图只挪了一点点、
    仍散在各自的原位。

    契约：`原坐标 + offset` 必须等于帧内坐标。
    """
    from services.axis_frame import build_axis_frame

    frame = build_axis_frame({
        "d1": {"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0, "B": 6.0}},
        # 页面原点偏了 500 米 —— 平移量必须包含这 500
        "d2": {"x": {"1": 500.0, "2": 508.0}, "y": {"A": 300.0, "B": 306.0}},
    })
    assert sorted(frame.members) == ["d1", "d2"]
    for did, obs in (("d1", {"x": 0.0, "y": 0.0}),
                     ("d2", {"x": 500.0, "y": 300.0})):
        off = frame.offsets[did]
        # 轴号 1 / A 在帧内是 0 —— 加上 offset 后必须落在 0
        assert obs["x"] + off["x"] == pytest.approx(0.0, abs=1e-6)
        assert obs["y"] + off["y"] == pytest.approx(0.0, abs=1e-6)


# ── 帧间配准 ──────────────────────────────────────────────────

@pytest.mark.unit
def test_frames_are_registered_to_each_other_by_shared_labels():
    """**帧内部干净不等于帧之间对齐。**

    每个帧以「本帧最小轴号 = 0」为原点，321 / 246 个帧就是
    321 / 246 个互不相干的原点。实测把构件换到帧内后
    包络/核心比不降反升（大歌剧院 3.99→4.85、轨道交通 3.05→8.42）——
    帧是散的。

    帧之间同样靠**共有轴号**配准：把每个帧的轴网当作一次观测，
    用同一套共识算法再上一层。
    """
    from services.axis_frame import AxisFrame, register_frames

    frames = [
        AxisFrame(axes={"x": {"1": 0.0, "2": 8.0, "3": 16.0},
                        "y": {"A": 0.0, "B": 6.0}}, members=["a"]),
        # 同一片轴网的另一个帧：只见到 2/3，自己归零后 2=0
        AxisFrame(axes={"x": {"2": 0.0, "3": 8.0},
                        "y": {"A": 0.0, "B": 6.0}}, members=["b"]),
    ]
    offsets = register_frames(frames)
    # 第二个帧要整体右移 8 米才与第一个帧的 2/3 对上
    assert offsets[1]["x"] == pytest.approx(8.0)
    assert offsets[1]["y"] == pytest.approx(0.0)
    assert offsets[0]["x"] == pytest.approx(0.0)


@pytest.mark.unit
def test_unregisterable_frame_gets_none_not_zero():
    """与任何帧都没有共有轴号时返回 None ——
    **偏移 0 会被下游当成「已配准到原点」**，而它其实是「没配准」。"""
    from services.axis_frame import AxisFrame, register_frames

    offsets = register_frames([
        AxisFrame(axes={"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0}}, members=["a"]),
        AxisFrame(axes={"x": {"88": 0.0, "99": 5.0}, "y": {"Z": 0.0}}, members=["b"]),
    ])
    assert offsets[0] == {"x": 0.0, "y": 0.0}
    assert offsets[1] is None


@pytest.mark.unit
def test_largest_frame_anchors_the_registration():
    """成员最多的帧当锚——它的证据最多，让它去迁就小帧没有道理。"""
    from services.axis_frame import AxisFrame, register_frames

    offsets = register_frames([
        AxisFrame(axes={"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0}}, members=["a"]),
        AxisFrame(axes={"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0}},
                  members=["b", "c", "d"]),
    ])
    assert offsets[1] == {"x": 0.0, "y": 0.0}      # 大帧不动
    assert offsets[0]["x"] == pytest.approx(0.0)


@pytest.mark.unit
def test_misregistered_frame_does_not_pollute_the_pool():
    """**「来者不拒」这个坑第三次出现了。**

    前两次：`_grow_consensus` 只看有没有共有轴号就并入（混两套轴网时
    返回 0 帧）；`_grow_joint` 分方向各自成团。
    这一次是帧间配准——每个配准过的帧的轴号都进池子却不校验残差，
    错配的帧污染池子后，后续帧跟着错（大歌剧院包络 833→1079 米）。

    规则：配准也要校验残差，不达标的不进池、不给偏移。
    """
    from services.axis_frame import AxisFrame, register_frames

    frames = [
        AxisFrame(axes={"x": {"1": 0.0, "2": 8.0, "3": 16.0}, "y": {"A": 0.0}},
                  members=["a", "b", "c"]),
        # 轴距完全不同 —— 不是同一片轴网，配不上
        AxisFrame(axes={"x": {"1": 0.0, "2": 40.0, "3": 80.0}, "y": {"A": 0.0}},
                  members=["x"]),
        # 与锚帧一致，应当配准成功
        AxisFrame(axes={"x": {"2": 0.0, "3": 8.0}, "y": {"A": 0.0}}, members=["y"]),
    ]
    offsets = register_frames(frames)
    assert offsets[1] is None, "轴距不符的帧不该被配准"
    assert offsets[2] is not None and offsets[2]["x"] == pytest.approx(8.0)


# ── K-3 重做：按结构关系决定谁能配准 ──────────────────────────

@pytest.mark.unit
def test_frames_across_stories_of_one_unit_register():
    """**同一单体的不同楼层本就共用一套轴网**——建筑是垂直对齐的，
    这类帧之间的配准是合法的。"""
    from services.axis_frame import AxisFrame, register_frames_by_structure

    frames = [
        (("F1", "main", 0), AxisFrame(axes={"x": {"1": 0.0, "2": 8.0},
                                            "y": {"A": 0.0}}, members=["a", "b"])),
        (("F2", "main", 0), AxisFrame(axes={"x": {"1": 0.0, "2": 8.0},
                                            "y": {"A": 0.0}}, members=["c", "d"])),
    ]
    offsets = register_frames_by_structure(frames)
    assert offsets[0] is not None and offsets[1] is not None


@pytest.mark.unit
def test_zone_frames_within_one_story_do_not_register():
    """**同一楼层的不同分区是不同轴网**（GB/T 50001 §8.0.5：
    一图三套轴网），共用轴号名却不共用轴网。

    强行配准的代价实测过：宽松则污染（包络 833→1079 米），
    严格则归零（落库摆放 1394→157）。所以按结构关系直接排除。
    """
    from services.axis_frame import AxisFrame, register_frames_by_structure

    frames = [
        (("F1", "main", 0), AxisFrame(axes={"x": {"1": 0.0, "2": 8.0},
                                            "y": {"A": 0.0}}, members=["a", "b"])),
        # 同层第二套轴网 = 另一个分区
        (("F1", "main", 1), AxisFrame(axes={"x": {"1": 0.0, "2": 8.0},
                                            "y": {"A": 0.0}}, members=["c"])),
    ]
    offsets = register_frames_by_structure(frames)
    assert offsets[0] is not None
    assert offsets[1] is None, "同层分区帧不该被配准"


@pytest.mark.unit
def test_different_units_do_not_register():
    """南区与北区各有各的原点——跨单体配准会把两栋楼摞在一起。"""
    from services.axis_frame import AxisFrame, register_frames_by_structure

    frames = [
        (("F1", "south", 0), AxisFrame(axes={"x": {"1": 0.0}, "y": {"A": 0.0}},
                                       members=["a", "b"])),
        (("F1", "north", 0), AxisFrame(axes={"x": {"1": 0.0}, "y": {"A": 0.0}},
                                       members=["c", "d"])),
    ]
    offsets = register_frames_by_structure(frames)
    # 各自成锚，互不配准
    assert offsets[0] == {"x": 0.0, "y": 0.0}
    assert offsets[1] == {"x": 0.0, "y": 0.0}


@pytest.mark.unit
def test_empty_input_is_safe():
    from services.axis_frame import register_frames_by_structure

    assert register_frames_by_structure([]) == []
    assert register_frames_by_structure(None) == []


@pytest.mark.unit
def test_label_registration_seeds_from_already_pinned_frames():
    """**两级配准必须落在同一个参照系里。**

    实测教训：锚点给的是测量坐标（几十万米量级），
    轴号配准另起一个 0 点给的是帧内局部坐标——两者混进同一个场景，
    内容被隔开几公里（大歌剧院包络 6563 米，而建筑实际约 200 米）。

    所以轴号配准要**以已钉住的帧为锚**，而不是自己挑一个最大的帧当 0 点。
    """
    from services.axis_frame import AxisFrame, register_frames

    frames = [
        # 已被世界锚点钉住：轴号 1 在世界坐标 1000
        AxisFrame(axes={"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0}}, members=["a"]),
        AxisFrame(axes={"x": {"2": 0.0, "3": 8.0}, "y": {"A": 0.0}},
                  members=["b", "c", "d"]),   # 成员更多，但不该当锚
    ]
    offsets = register_frames(frames, seeds={0: {"x": 1000.0, "y": 500.0}})
    assert offsets[0] == {"x": 1000.0, "y": 500.0}
    # 第二个帧的轴号 2 对应第一个帧的 x=8 → 世界 1008
    assert offsets[1]["x"] == pytest.approx(1008.0)
