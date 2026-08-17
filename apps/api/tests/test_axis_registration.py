"""同名轴线配准 + 质量评估单测(多图拼接成完整模型的关键)。纯函数。"""
from services.axis_registration import align_by_shared_axes, merge_axes


def _axes(x_items, y_items):
    return {"x": [{"label": l, "coord": c} for l, c in x_items],
            "y": [{"label": l, "coord": c} for l, c in y_items]}


def test_clean_translation_is_accepted():
    """两图同名轴线整体平移 10m/5m → 配准成立。"""
    ref = _axes([("1", 0.0), ("2", 6.0), ("3", 12.0)], [("A", 0.0), ("B", 6.0)])
    cur = _axes([("1", -10.0), ("2", -4.0), ("3", 2.0)], [("A", -5.0), ("B", 1.0)])
    r = align_by_shared_axes(ref, cur)
    assert r["ok"] is True
    assert r["dx"] == 10.0 and r["dy"] == 5.0
    assert r["shared_x"] == 3 and r["residual_x"] == 0.0


def test_reject_single_shared_axis():
    """仅 1 条同名轴线 → 无法校验一致性,拒绝(错位拼接比不拼更糟)。"""
    ref = _axes([("1", 0.0)], [])
    cur = _axes([("1", -10.0)], [])
    r = align_by_shared_axes(ref, cur)
    assert r["ok"] is False
    assert "无法校验一致性" in r["reason"]


def test_reject_inconsistent_offsets():
    """各同名轴线位置差不一致(比例不同/轴号误识)→ 拒绝硬平移。"""
    ref = _axes([("1", 0.0), ("2", 6.0), ("3", 12.0)], [])
    cur = _axes([("1", -10.0), ("2", -2.0), ("3", 8.0)], [])   # 差 10/8/4 不一致
    r = align_by_shared_axes(ref, cur)
    assert r["ok"] is False
    assert "残差" in r["reason"]


def test_no_shared_axes():
    r = align_by_shared_axes(_axes([("1", 0.0)], []), _axes([("9", 5.0)], []))
    assert r["ok"] is False
    assert "无同名轴线" in r["reason"]


def test_tolerates_small_jitter():
    """OCR 抖动导致的小残差(<1m)仍接受。"""
    ref = _axes([("1", 0.0), ("2", 6.0), ("3", 12.0)], [])
    cur = _axes([("1", -10.0), ("2", -4.1), ("3", 1.95)], [])
    r = align_by_shared_axes(ref, cur)
    assert r["ok"] is True


def test_accepts_legacy_tuple_structure():
    """兼容既有 [[label,pos]] 结构(register_offset 用的格式)。"""
    ref = {"x": [["1", 0.0], ["2", 6.0]], "y": []}
    cur = {"x": [["1", -3.0], ["2", 3.0]], "y": []}
    r = align_by_shared_axes(ref, cur)
    assert r["ok"] is True and r["dx"] == 3.0


def test_merge_axes_union_with_shift():
    """并集:同名取参考值,新轴号按平移量补入(单图常只画本区域轴线)。"""
    ref = _axes([("1", 0.0), ("2", 6.0)], [("A", 0.0)])
    cur = _axes([("2", -4.0), ("3", 2.0)], [("B", 1.0)])
    merged = merge_axes(ref, cur, dx=10.0, dy=5.0)
    xs = {e["label"]: e["coord"] for e in merged["x"]}
    assert xs["1"] == 0.0 and xs["2"] == 6.0      # 同名保留参考值
    assert xs["3"] == 12.0                        # 新轴号平移补入
    ys = {e["label"]: e["coord"] for e in merged["y"]}
    assert ys["A"] == 0.0 and ys["B"] == 6.0


def test_filter_removes_clustered_fake_axes():
    """实测:图框文字被误识为轴号,坐标全挤在 2m 内 → 整簇丢弃。"""
    from services.axis_registration import filter_real_axes
    fake = {"x": [{"label": "0", "coord": 169.44}, {"label": "1", "coord": 168.97},
                  {"label": "3", "coord": 168.51}, {"label": "4", "coord": 170.38}], "y": []}
    assert filter_real_axes(fake, "x") == []


def test_filter_keeps_real_grid():
    """真实轴网:间距 6-8m、跨度足够 → 保留。"""
    from services.axis_registration import filter_real_axes
    real = {"x": [{"label": "1", "coord": 0.0}, {"label": "2", "coord": 6.0},
                  {"label": "3", "coord": 13.0}, {"label": "4", "coord": 20.0}], "y": []}
    kept = filter_real_axes(real, "x")
    assert len(kept) == 4
    assert kept[0][0] == "1"


def test_filter_dedupes_cluster_but_keeps_spread():
    """混合:真轴线保留,挤成簇的只留代表。"""
    from services.axis_registration import filter_real_axes
    mixed = {"x": [{"label": "1", "coord": 0.0}, {"label": "2", "coord": 6.0},
                   {"label": "x1", "coord": 6.3}, {"label": "3", "coord": 14.0}], "y": []}
    kept = filter_real_axes(mixed, "x")
    assert [l for l, _ in kept] == ["1", "2", "3"]      # 6.3 与 6.0 同簇被并


def test_clean_axes_structure():
    from services.axis_registration import clean_axes
    out = clean_axes({"x": [{"label": "1", "coord": 0.0}, {"label": "2", "coord": 12.0}],
                      "y": [{"label": "A", "coord": 1.0}, {"label": "B", "coord": 1.5}]})
    assert len(out["x"]) == 2
    assert out["y"] == []        # y 向挤在一起 → 判无效
