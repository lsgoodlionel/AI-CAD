"""建模的比例门禁：低置信变换不当权威，离群图不污染场景包络。

**实测**（轨道交通工程 v1，用户反馈「完全失真、没有标高、积压在一起」）：

| | 全场景包络 | 中间 90% 的点 | 最大单图跨度 |
|---|---|---|---|
| 轨道交通 | **4862 × 3701 m** | 762 × 534 m | **4176.7 m** |
| 大歌剧院 | 589 × 765 m | 214 × 137 m | 574.1 m |

场景被 2 张离群图撑到 **4.8 公里**，而真实内容只有 760 米——
建筑缩成中间一小团。**这就是「积压在一起」的机制**：
不是渲染问题，是包络被离群值撑爆。

根因在上游：`drawing_transform` 里 633 张来自 `geometry`（图幅推断）的
比例跨越三个数量级（0.001~1.707 m/pt），**平均置信 0.02**，
却被 `_scale_override_of` **无条件**交给识别器当权威。
而来自 `axes`（轴号识别）的 80 张中位正好是 1:100、平均置信 0.97。
"""
import pytest


# ── A. 变换置信门禁 ───────────────────────────────────────────

@pytest.mark.unit
def test_high_confidence_standard_scale_is_trusted():
    """轴号识别出的 1:100、置信 0.97 —— 正是要用的。"""
    from core.model3d.scale_gate import is_transform_trustworthy

    assert is_transform_trustworthy(scale_m_pt=0.03528, confidence=0.97)


@pytest.mark.unit
def test_low_confidence_transform_is_rejected():
    """**实测 607/633 张 `geometry` 变换置信 < 0.1** —— 用它还不如不用：
    不用时识别器至少按图纸自身内容估，用了就被钉死在错误尺度上。"""
    from core.model3d.scale_gate import is_transform_trustworthy

    assert not is_transform_trustworthy(scale_m_pt=0.03528, confidence=0.02)


@pytest.mark.unit
@pytest.mark.parametrize("scale", [1.70731, 0.00086, 0.0])
def test_implausible_scale_is_rejected_regardless_of_confidence(scale):
    """1.707 m/pt ≈ 1:4838，0.00086 ≈ 1:2.4 —— 建筑图不会是这个比例。
    置信度再高也不行：置信衡量的是「识别质量」，不是「比例对不对」。"""
    from core.model3d.scale_gate import is_transform_trustworthy

    assert not is_transform_trustworthy(scale_m_pt=scale, confidence=1.0)


@pytest.mark.unit
def test_missing_confidence_is_not_treated_as_perfect():
    """置信为空时不能当满分——那正是「没人评估过」的意思。"""
    from core.model3d.scale_gate import is_transform_trustworthy

    assert not is_transform_trustworthy(scale_m_pt=0.03528, confidence=None)


# ── B. 跨度离群 ───────────────────────────────────────────────

@pytest.mark.unit
def test_outlier_span_is_flagged():
    """判据来自实测：**效果尚可的大歌剧院最大只有中位的 7.51 倍**，
    而轨道交通有 39.8 倍。取 8 倍，对大歌剧院零影响。"""
    from core.model3d.scale_gate import outlier_sources

    spans = {"a": 100.0, "b": 105.0, "c": 110.0, "bad": 4176.7}
    assert outlier_sources(spans) == {"bad"}


@pytest.mark.unit
def test_good_project_loses_nothing():
    """大歌剧院实测分布 P100 = 中位的 7.51 倍 —— 不得误杀。"""
    from core.model3d.scale_gate import outlier_sources

    spans = {f"d{i}": v for i, v in enumerate(
        [2.2, 5.3, 39.7, 76.4, 76.7, 123.6, 225.5, 254.5, 574.1])}
    assert outlier_sources(spans) == set()


@pytest.mark.unit
def test_degenerate_spans_are_not_outliers():
    """跨度 0 的图（实测 3 张）是另一类问题，不该被当成尺度离群——
    把它们混进来会让「离群」这个词失去含义。"""
    from core.model3d.scale_gate import outlier_sources

    assert outlier_sources({"a": 100.0, "b": 0.0, "c": 105.0}) == set()


@pytest.mark.unit
def test_too_few_sources_yields_no_outliers():
    """样本太少时中位数没有意义——宁可不判，也不要凭两张图断定谁离群。"""
    from core.model3d.scale_gate import outlier_sources

    assert outlier_sources({"a": 10.0, "b": 4000.0}) == set()


@pytest.mark.unit
def test_empty_input_is_safe():
    from core.model3d.scale_gate import outlier_sources

    assert outlier_sources({}) == set()
    assert outlier_sources(None) == set()


# ── 接线 ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_scale_override_respects_the_gate():
    """**这是根因所在**：`_scale_override_of` 此前无条件把落库比例
    交给识别器当权威，633 张置信 0.02 的垃圾变换正是这样进入建模的。"""
    from services.model_elements import _scale_override_of

    class T:
        def __init__(self, scale, conf):
            self.scale_m_pt, self.confidence = scale, conf

    good = {"d1": T(0.03528, 0.97)}
    bad = {"d1": T(0.03528, 0.02)}
    absurd = {"d1": T(1.70731, 1.0)}
    assert _scale_override_of(good, "d1") == 0.03528
    assert _scale_override_of(bad, "d1") is None
    assert _scale_override_of(absurd, "d1") is None
    assert _scale_override_of(None, "d1") is None


@pytest.mark.unit
def test_scene_marks_outlier_sources_instead_of_deleting_them():
    """离群图**标记而不删除**：删了就看不见问题在哪，
    而项目的既有约束是「降级必须可见」。
    标记后前端算包络时跳过它们，相机才能框住真实内容。
    """
    from services.model_elements import mark_scale_outliers

    floors = [{"elements": {"columns": [
        {"src": "ok1", "outline": [[0, 0], [100, 100]]},
        {"src": "ok2", "outline": [[0, 0], [105, 105]]},
        {"src": "ok3", "outline": [[0, 0], [110, 110]]},
        {"src": "ok4", "outline": [[0, 0], [98, 98]]},
        {"src": "bad", "outline": [[0, 0], [4176, 4176]]},
    ]}}]
    suspects = mark_scale_outliers(floors)
    assert suspects == {"bad"}
    columns = floors[0]["elements"]["columns"]
    assert [c["src"] for c in columns] == ["ok1", "ok2", "ok3", "ok4", "bad"]
    assert columns[-1]["scale_suspect"] is True
    assert all("scale_suspect" not in c for c in columns[:-1])


@pytest.mark.unit
def test_span_counts_both_outline_and_path():
    """**墙/梁/管线用的是 `path`，柱/板/设备用 `outline`**（前端
    `elementsBounds` 就是这么读的）。只读 `outline` 会让墙梁管整类
    不参与跨度统计——而实测 B1 层有 594 面墙、288 根梁，
    正是它们最可能把包络撑开。
    """
    from services.model_elements import mark_scale_outliers

    floors = [{"elements": {
        "columns": [{"src": f"ok{i}", "outline": [[0, 0], [100, 100]]}
                    for i in range(4)],
        "walls": [{"src": "badwall", "path": [[0, 0], [4176, 4176]]}],
    }}]
    assert mark_scale_outliers(floors) == {"badwall"}
    assert floors[0]["elements"]["walls"][0]["scale_suspect"] is True


# ── 选图覆盖面 ────────────────────────────────────────────────

@pytest.mark.unit
def test_architecture_plans_are_not_dropped():
    """**实测**：轨道交通的平面图按专业分布为
    mep 313 / architecture 49 / structure 49 / decoration 32，
    而分桶只认 `mep` / `beam` / `structure` 三类 ——
    **architecture 49 + decoration 32 = 81 张平面图被整张丢弃**，
    而建筑平面图正是墙与门窗的主要来源。

    判据还不自洽：结构平面图没有世界坐标照样进，
    建筑平面图没有世界坐标就被拒（理由是「位置不可信会添噪声」）——
    但实测 `placed_drawings = 0`，**没有任何一张图有世界坐标**，
    这条理由对两者同样成立，不该只用来挡其中一类。
    """
    from services.model_elements import pick_element_drawings

    floor = [
        {"id": "a1", "title": "地下二层平面图", "discipline": "architecture"},
        {"id": "s1", "title": "地下二层结构平面图", "discipline": "structure"},
        {"id": "m1", "title": "地下二层给排水平面图", "discipline": "mep"},
        {"id": "d1", "title": "地下二层精装平面图", "discipline": "decoration"},
    ]
    buckets = pick_element_drawings(floor)
    picked = {d["id"] for group in buckets.values() for d in group}
    assert "a1" in picked, "建筑平面图被丢弃——墙的主要来源"
    assert "d1" in picked, "装修平面图被丢弃"
    assert {"s1", "m1"} <= picked


@pytest.mark.unit
def test_non_plan_architecture_drawings_stay_out():
    """只对**平面图**开这个口子——立面/详图/节点进来只会添噪声，
    它们本就不该产出楼层构件。"""
    from services.model_elements import pick_element_drawings

    floor = [
        {"id": "e1", "title": "南立面图", "discipline": "architecture"},
        {"id": "j1", "title": "C1玻璃幕墙横剖节点详图", "discipline": "architecture"},
        {"id": "a1", "title": "五层平面图", "discipline": "architecture"},
    ]
    buckets = pick_element_drawings(floor)
    picked = {d["id"] for group in buckets.values() for d in group}
    assert picked == {"a1"}


@pytest.mark.unit
def test_architecture_bucket_is_actually_consumed():
    """**整条传递链都要守**：消费方原本只列 structure/beam/mep 三个桶，
    新加的桶会被**静默忽略**——「加了桶没人取」等于没加。"""
    import inspect

    import services.model_elements as me

    source = inspect.getsource(me)
    assert 'picked["architecture"]' in source, "新桶没有被消费"
    # 建筑平面图产出的是墙/柱/板，与结构同类
    plan = source.split('all_picked = ')[1][:900]
    assert '"architecture"' in plan


@pytest.mark.unit
def test_suspect_elements_are_reported_as_a_quality_issue():
    """**只不参与包络还不够**：实测那 11 张离群图产出了 6411 个构件，
    占全部 32512 的 **20%** —— 它们以错误尺寸散落在 4 公里范围内，
    照常渲染的话用户看到的依然是「失真」。

    所以要既**隐藏**（不污染视图）又**可见**（质量面板明说），
    而不是二选一。
    """
    from services.model_elements import scale_suspect_summary

    floors = [{"elements": {
        "columns": [{"src": "ok", "outline": [[0, 0], [1, 1]]},
                    {"src": "bad", "outline": [[0, 0], [1, 1]],
                     "scale_suspect": True}],
        "walls": [{"src": "bad", "path": [[0, 0], [1, 1]], "scale_suspect": True}],
    }}]
    summary = scale_suspect_summary(floors, {"bad"})
    assert summary["drawings"] == 1
    assert summary["elements"] == 2
    assert summary["total_elements"] == 3
    assert summary["ratio"] == pytest.approx(2 / 3)


@pytest.mark.unit
def test_no_suspects_yields_zeroed_summary():
    """没有离群时也要给出结构，别让前端判 undefined。"""
    from services.model_elements import scale_suspect_summary

    summary = scale_suspect_summary([], set())
    assert summary == {"drawings": 0, "elements": 0,
                       "total_elements": 0, "ratio": 0.0}
