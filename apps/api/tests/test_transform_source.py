"""变换要记住**是谁写的** —— 否则陈旧变换既不敢删也不敢留(J7 后续)。

**实测缺陷**:`S-0-20-102.04C` 的轴网识别跑于 **06:02**,
而它的 `drawing_transform` 停在 **01:47** —— 下游一直在用那条
过时的、`origin_x=0` 的变换。全项目 **48 张** origin_x=0 且仍有轴网。

根因不是「值算错了」,而是**算不出新值时旧值不失效**:
`transform_from_axes` 本身是对的(缺方向返回 None、不落无效变换),
但它返回 None 时没人去清理上一次留下的行。

**为什么不能直接删**:`drawing_transform` 主键是 `drawing_id`,
一图一行,而**三条路径都往这一行写**:

| 来源 | 写入点 |
|---|---|
| `axes` | `tasks/axis_recognition.py`（Phase I 轴网路径）|
| `geometry` | `tasks/drawing_info_extract.py`（图面文字读比例）|
| `manual` | `routers/project_info.py` 三个人工确认端点 |

轴网路径算不出时若无脑删,可能删掉**几何路径的合法产出**,
更可能删掉**人工确认过的比例尺**。所以先有来源,才谈得上清理。

**历史行的来源不可考** —— 一律记 `unknown`,不猜。
"""
from __future__ import annotations

import pytest

from services.drawing_transform import (
    TRANSFORM_SOURCE_AXES, TRANSFORM_SOURCE_GEOMETRY, TRANSFORM_SOURCE_MANUAL,
    TRANSFORM_SOURCE_UNKNOWN, DrawingTransform,
)


def _t(**kw) -> DrawingTransform:
    base = dict(scale_m_pt=0.01, origin_x=1.0, origin_y=2.0, page_h=1000.0)
    return DrawingTransform(**{**base, **kw})


# ── 来源要落库、要读得回来 ────────────────────────────────────────

@pytest.mark.unit
def test_source_defaults_to_unknown():
    """不声明来源就是 unknown —— **判不出就说判不出**,不猜。"""
    assert _t().source == TRANSFORM_SOURCE_UNKNOWN


@pytest.mark.asyncio
async def test_persist_writes_the_source(fake_db):
    from services.drawing_transform import persist_transform

    await persist_transform(fake_db, project_id="p1", drawing_id="d1",
                            transform=_t(source=TRANSFORM_SOURCE_AXES))
    assert fake_db.execute.call_args.args[1]["source"] == TRANSFORM_SOURCE_AXES


@pytest.mark.asyncio
async def test_fetch_reads_the_source_back(fake_db):
    """**写了读不回来等于没写** —— 这一类缺口在本项目已出现五次。"""
    from services.drawing_transform import fetch_project_transforms

    fake_db.fetch_all.return_value = [{
        "drawing_id": "d1", "scale_m_pt": 0.01, "origin_x": 1.0, "origin_y": 2.0,
        "page_h": 1000.0, "confidence": 0.9, "source": TRANSFORM_SOURCE_MANUAL,
        "origin_x_estimated": False, "origin_y_estimated": False,
    }]
    got = await fetch_project_transforms(fake_db, "p1")
    assert got["d1"].source == TRANSFORM_SOURCE_MANUAL


@pytest.mark.asyncio
async def test_fetch_reads_the_estimated_flags_back(fake_db):
    """**同一形状的既有缺口**:`origin_*_estimated` 落了库却没读回来。

    上一轮加了列、加了 SELECT,却漏了构造对象时的赋值 ——
    于是下游(包络/校验)永远看不到「这个方向的原点是兜底的」。
    """
    from services.drawing_transform import fetch_project_transforms

    fake_db.fetch_all.return_value = [{
        "drawing_id": "d1", "scale_m_pt": 0.01, "origin_x": 0.0, "origin_y": 2.0,
        "page_h": 1000.0, "confidence": 1.0, "source": TRANSFORM_SOURCE_AXES,
        "origin_x_estimated": True, "origin_y_estimated": False,
    }]
    got = await fetch_project_transforms(fake_db, "p1")
    assert got["d1"].origin_x_estimated is True
    assert got["d1"].origin_y_estimated is False


@pytest.mark.asyncio
async def test_fetch_tolerates_rows_without_the_column(fake_db):
    """迁移未跑时不能炸 —— 缺列按 unknown 处理。"""
    from services.drawing_transform import fetch_project_transforms

    fake_db.fetch_all.return_value = [{
        "drawing_id": "d1", "scale_m_pt": 0.01, "origin_x": 1.0, "origin_y": 2.0,
        "page_h": 1000.0, "confidence": None,
    }]
    got = await fetch_project_transforms(fake_db, "p1")
    assert got["d1"].source == TRANSFORM_SOURCE_UNKNOWN


# ── 清理只动自己写的那条 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_only_removes_the_same_source(fake_db):
    """**核心用例**:轴网路径清理时,不得删掉几何/人工的产出。"""
    from services.drawing_transform import clear_transform

    await clear_transform(fake_db, drawing_id="d1", source=TRANSFORM_SOURCE_AXES)
    sql, params = fake_db.execute.call_args.args[:2]
    assert "DELETE FROM drawing_transform" in sql
    assert "source" in sql, "必须按来源限定,否则会删掉别的路径的合法变换"
    assert params["source"] == TRANSFORM_SOURCE_AXES


@pytest.mark.asyncio
async def test_clear_does_not_touch_unknown_rows(fake_db):
    """来源不可考的历史行**不清** —— 不知道是谁写的就不能替它做主。

    实测 1436 条历史变换全部无来源。一次性清空会让这些图
    立刻失去定位,而其中相当一部分是对的。
    """
    from services.drawing_transform import clear_transform

    await clear_transform(fake_db, drawing_id="d1", source=TRANSFORM_SOURCE_AXES)
    sql = fake_db.execute.call_args.args[0]
    assert "= :source" in sql, "只删来源相等的行,unknown 不在其列"


# ── 人工确认不被自动路径覆盖 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_manual_transform_is_not_overwritten_by_automatic_ones(fake_db):
    """人工确认过的比例尺是**人核过的真值**,重跑识别不该把它冲掉。

    与 `filter_scene_axes` 对 `axes_source == "manual"` 的处置同源:
    自动校验是用来挡自动识别的错值,不该推翻人的判断。
    """
    from services.drawing_transform import persist_transform

    await persist_transform(fake_db, project_id="p1", drawing_id="d1",
                            transform=_t(source=TRANSFORM_SOURCE_GEOMETRY))
    sql = fake_db.execute.call_args.args[0]
    assert TRANSFORM_SOURCE_MANUAL in sql, "upsert 要保住人工确认的行"


@pytest.mark.asyncio
async def test_manual_can_still_overwrite_anything(fake_db):
    """人工要能改掉任何自动值 —— 否则人工通道就失效了。"""
    from services.drawing_transform import persist_transform

    await persist_transform(fake_db, project_id="p1", drawing_id="d1",
                            transform=_t(source=TRANSFORM_SOURCE_MANUAL))
    params = fake_db.execute.call_args.args[1]
    assert params["source"] == TRANSFORM_SOURCE_MANUAL


@pytest.mark.asyncio
async def test_clear_never_removes_manual(fake_db):
    """自动路径清理不得波及人工确认。"""
    from services.drawing_transform import clear_transform

    await clear_transform(fake_db, drawing_id="d1", source=TRANSFORM_SOURCE_AXES)
    params = fake_db.execute.call_args.args[1]
    assert params["source"] != TRANSFORM_SOURCE_MANUAL


# ── 算不出新变换时，旧的要失效 ────────────────────────────────────

@pytest.mark.unit
def test_recognition_task_clears_when_it_cannot_solve():
    """**核心缺陷用例**：轴网路径算不出变换时要清掉自己上次写的行。

    实测 `S-0-20-102.04C`：识别跑于 06:02、变换停在 01:47，
    下游一直在用那条过时的、`origin_x=0` 的变换。
    「宁可没有变换，也不能有错变换」——没有时下游会降级估位置，
    而过时的会带着置信度骗过所有下游。
    """
    import inspect

    from tasks import axis_recognition

    src = inspect.getsource(axis_recognition)
    assert "clear_transform" in src, "算不出时必须清理，否则旧值一直生效"


@pytest.mark.unit
def test_recognition_task_clears_only_its_own_source():
    """清理要限定 `axes` —— 不得连坐几何路径或人工确认。"""
    import inspect

    from tasks import axis_recognition

    src = inspect.getsource(axis_recognition)
    assert "TRANSFORM_SOURCE_AXES" in src


# ── 两条路径各握一半时要合起来用 ──────────────────────────────────

@pytest.mark.unit
def test_axes_path_borrows_the_scale_when_it_has_none():
    """**核心用例**：轴网路径有原点没比例时，借用已落库的比例。

    实测 `S-0-20-102.04C`：

    | 路径 | 比例 | 原点 |
    |---|---|---|
    | 几何 | ✅ 1:150（§6.0.4 标准值）| ❌ `origin_x=0`（`_min_labeled_pos` 缺陷）|
    | 轴网 | ❌ 无坐标标注，RANSAC 无从拟合 | ✅ 数字向 60 条 + 字母向 9 条 |

    旧实现「自身没比例就整条放弃」，白白丢掉轴网算对的原点。
    全项目 **149 张**原点缺一向的图里 **60 张**轴网是双向的。
    """
    from tasks.axis_recognition import _transform_of

    result = {"transform": {}, "page_h": 1000.0,
              "axes": [{"label_kind": "numeric", "offset_pt": -200.0},
                       {"label_kind": "alpha", "offset_pt": 300.0}]}
    got = _transform_of(result, _fake_builder, fallback_scale=0.05291666)
    assert got is not None, "有原点又借得到比例，就该落变换"
    assert got.scale_m_pt == pytest.approx(0.05291666)


@pytest.mark.unit
def test_borrowed_scale_must_pass_the_sanity_gate():
    """借来的比例同样要过 §6.0.4 门禁 —— 历史行可能是门禁之前写的。

    1:335 万那批就是这么来的（见 `drawing_transform` 的比例门禁）。
    """
    from tasks.axis_recognition import _transform_of

    result = {"transform": {}, "page_h": 1000.0,
              "axes": [{"label_kind": "numeric", "offset_pt": -200.0},
                       {"label_kind": "alpha", "offset_pt": 300.0}]}
    assert _transform_of(result, _fake_builder, fallback_scale=1184.0) is None


@pytest.mark.unit
def test_own_scale_wins_over_the_borrowed_one():
    """自己拟合出的比例更可信（RANSAC 残差 5.7 毫米），不被借来的顶替。"""
    from tasks.axis_recognition import _transform_of

    result = {"transform": {"scale_m_pt": 0.142757}, "page_h": 1000.0,
              "axes": [{"label_kind": "numeric", "offset_pt": -200.0},
                       {"label_kind": "alpha", "offset_pt": 300.0}]}
    got = _transform_of(result, _fake_builder, fallback_scale=0.05)
    assert got.scale_m_pt == pytest.approx(0.142757)


@pytest.mark.unit
def test_no_scale_anywhere_still_yields_nothing():
    """两边都没有比例 ⇒ **判不出就说判不出**，不落变换。"""
    from tasks.axis_recognition import _transform_of

    result = {"transform": {}, "page_h": 1000.0, "axes": []}
    assert _transform_of(result, _fake_builder, fallback_scale=None) is None


def _fake_builder(axes, *, page_h, scale_m_pt):
    from services.axis_world_anchors import transform_from_axes

    return transform_from_axes(axes, page_h=page_h, scale_m_pt=scale_m_pt)
