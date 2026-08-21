"""Phase K-2：轴网帧的持久化与消费契约。

K-1 实测（蓝图 §7）：二维联合聚类后大歌剧院 **86%** 的图落在
有交叉约束的帧里、残差中位 0.1 毫米、P95 17 厘米。

K-2 把帧落库，让构件坐标能从「各图自己的局部坐标系」
换到「工程自有坐标系」——**全程不需要一个测量坐标**。
"""
import pytest


@pytest.mark.unit
def test_frame_rows_carry_the_group_key():
    """帧只在同一层同一单体内成立——**分组是必要条件不是优化项**，
    实测不分组时 96% 的图残差超阈。"""
    from services.axis_frame_repo import build_frame_rows
    from services.axis_frame import AxisFrame

    frame = AxisFrame(axes={"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0}},
                      members=["d1", "d2"])
    rows = build_frame_rows("p1", "F3", "north", [frame])
    assert rows[0]["story_key"] == "F3"
    assert rows[0]["building_unit"] == "north"
    assert rows[0]["frame_index"] == 0
    assert rows[0]["member_count"] == 2


@pytest.mark.unit
def test_frames_are_indexed_by_size_descending():
    """0 号是成员最多的主轴网——下游默认取它。"""
    from services.axis_frame_repo import build_frame_rows
    from services.axis_frame import AxisFrame

    rows = build_frame_rows("p1", "F1", "-", [
        AxisFrame(axes={"x": {}, "y": {}}, members=["a"]),
        AxisFrame(axes={"x": {}, "y": {}}, members=["b", "c", "d"]),
    ])
    assert [r["frame_index"] for r in rows] == [0, 1]
    assert rows[0]["member_count"] == 3


@pytest.mark.unit
def test_placement_rows_carry_frame_size():
    """**单成员帧没有交叉约束**——一张图自己跟自己对齐，
    残差恒 0 却不构成任何证据。消费方要能区分，
    否则「进帧率」会被灌水（实测含单成员时 98%，实际有约束的 86%）。
    """
    from services.axis_frame_repo import build_placement_rows
    from services.axis_frame import AxisFrame

    frame = AxisFrame(members=["d1", "solo"],
                      offsets={"d1": {"x": 1.0, "y": 2.0}},
                      residuals={"d1": 0.003})
    rows = {r["drawing_id"]: r for r in build_placement_rows("f1", frame)}
    assert rows["d1"]["frame_size"] == 2
    assert rows["d1"]["offset_x"] == 1.0 and rows["d1"]["offset_y"] == 2.0
    assert rows["d1"]["residual_m"] == 0.003


@pytest.mark.unit
def test_missing_offset_is_not_faked_as_zero():
    """没算出平移量的图**不落库**——偏移 0 会被下游当成「已对齐」，
    而它其实是「没对齐」。判不出就说判不出。"""
    from services.axis_frame_repo import build_placement_rows
    from services.axis_frame import AxisFrame

    frame = AxisFrame(members=["d1", "d2"], offsets={"d1": {"x": 1.0, "y": 2.0}})
    ids = [r["drawing_id"] for r in build_placement_rows("f1", frame)]
    assert ids == ["d1"]


@pytest.mark.unit
def test_empty_frames_produce_no_rows():
    from services.axis_frame_repo import build_frame_rows, build_placement_rows
    from services.axis_frame import AxisFrame

    assert build_frame_rows("p", "F1", "-", []) == []
    assert build_placement_rows("f", AxisFrame()) == []


# ── 消费：构件坐标 → 帧内坐标 ─────────────────────────────────

@pytest.mark.unit
def test_points_are_shifted_into_the_frame():
    from services.axis_frame_repo import to_frame_coords

    pts = to_frame_coords([[10.0, 20.0], [12.0, 24.0]], {"offset_x": 5.0,
                                                          "offset_y": -3.0})
    assert pts == [[15.0, 17.0], [17.0, 21.0]]


@pytest.mark.unit
def test_without_a_placement_points_are_returned_unchanged():
    """没有摆放信息时原样返回——**不能悄悄挪到 0 点**，
    那会让「没对齐的图」看起来像「对齐到原点的图」。"""
    from services.axis_frame_repo import to_frame_coords

    assert to_frame_coords([[1.0, 2.0]], None) == [[1.0, 2.0]]


@pytest.mark.unit
def test_unregistered_frames_still_get_placements():
    """**帧内一致性与帧间配准是两件事，不能一起丢。**

    K-1 实测帧内残差毫米级、覆盖 86%；K-3 帧间配准实测只有 12% 的图
    落在已配准的帧里。此前对未配准的帧直接不落摆放——
    **把 K-1 的成果也丢了**（落库摆放 1394 → 171）。

    正确做法：都落库，用 `registered` 标出分界。
    帧内一致可用于同帧构件的相对关系；帧间未配准则不能跨帧摆放。
    """
    from services.axis_frame_repo import build_placement_rows
    from services.axis_frame import AxisFrame

    frame = AxisFrame(members=["d1"], offsets={"d1": {"x": 1.0, "y": 2.0}},
                      residuals={"d1": 0.001})
    rows = build_placement_rows("f1", frame, registered=False)
    assert rows[0]["registered"] is False
    assert rows[0]["offset_x"] == 1.0

    rows = build_placement_rows("f1", frame, registered=True)
    assert rows[0]["registered"] is True
