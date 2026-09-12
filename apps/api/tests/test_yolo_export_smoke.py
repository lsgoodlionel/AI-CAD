"""YOLO 导出真的能导出东西 —— 端到端冒烟，不只测切块算术。

`test_training_tiles.py` 只测 `tile_origins` / `boxes_in_tile` 的算术，
从没有真正渲染过一块、写过一个文件。于是三个坏掉的地方一路没被发现：

1. 按比例定 DPI 那次提交用 `float(t)` 取比例，而 `t` 是 transform 表的
   **一行 dict** —— 抛 TypeError，被 `except Exception: continue` 吞掉，
   **每张图都被静默跳过**，导出结果是 0 张。
2. 就算过了那一关，`_export_tiles` 里的 `fitz` / `Image` / `ImageDraw` 只在
   `main()` 里局部 import，模块级根本没有这些名字 —— 第一块瓦片就 NameError。
3. 浮点 DPI 传给 `get_pixmap(dpi=)`（已由 `render_clip` 修掉）。

这里用内存里造的真 PDF 页真的跑一遍。
"""
from __future__ import annotations

import types

import pytest


@pytest.mark.unit
def test_tile_dpi_reads_scale_from_the_transform_row():
    """transform 表的一行是 dict —— 比例要按键取，不能把整行当成数。"""
    from scripts.model3d.export_yolo_dataset import tile_dpi_for

    row = {"drawing_id": "d", "scale_m_pt": 0.052917, "origin_x": 0, "origin_y": 0}
    dpi = tile_dpi_for(row)
    assert 140 < dpi < 170, f"1:150 应得约 152 DPI，实得 {dpi}"


@pytest.mark.unit
@pytest.mark.parametrize("row", [None, {}, {"scale_m_pt": None}, {"scale_m_pt": 0}])
def test_tile_dpi_falls_back_when_scale_missing(row):
    """缺比例时回落兜底 DPI，而不是抛错被外层吞掉、整张图消失。"""
    from scripts.model3d.export_yolo_dataset import tile_dpi_for

    assert tile_dpi_for(row) >= 100


@pytest.mark.unit
def test_export_tiles_really_writes_images_and_labels(tmp_path, monkeypatch):
    """在真 PDF 页上跑一遍 `_export_tiles`：必须真的写出图和标注。"""
    import fitz

    import scripts.model3d.export_yolo_dataset as ex
    from core.model3d.yolo_export import meters_to_page

    for sub in ("images", "labels", "verify"):
        (tmp_path / sub).mkdir()
    monkeypatch.setattr(ex, "OUT", str(tmp_path))

    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    scale = 0.0353                                   # 1:100
    cols = [{"outline": [[x, 5.0], [x + 0.6, 5.0], [x + 0.6, 5.6], [x, 5.6]]}
            for x in (2.0 + 2.5 * i for i in range(12))]
    fe = types.SimpleNamespace(columns=cols, walls=[], slabs=[], scale=scale,
                               origin_pt=(0.0, 0.0), page_h=595.0)

    n_tiles, n_boxes, _ = ex._export_tiles(page, fe, "d" * 36, "smoke", 100.0, 0, meters_to_page)
    assert n_tiles >= 1 and n_boxes >= 12
    assert list((tmp_path / "images").glob("*.png")), "没写出任何图"
    assert list((tmp_path / "labels").glob("*.txt")), "没写出任何标注"


@pytest.mark.unit
def test_output_dir_is_not_the_old_trained_dataset():
    """默认输出目录不能是 `/tmp/yolo_ds3` —— 那里是现役模型的 76 张训练图，
    新瓦片写进去会和旧的整页图混在一起。"""
    import scripts.model3d.export_yolo_dataset as ex

    assert ex.OUT != "/tmp/yolo_ds3"


# ── 比例不合国标时不能照单全收 ────────────────────────────────────

@pytest.mark.unit
def test_non_standard_scale_falls_back_instead_of_exploding():
    """真实冒烟撞见的：「基础底板换撑平面布置图」库里的比例是 1:2465。

    这种平面图该是 1:100~1:200，2465 也不在 GB/T 50001 §6.0.4 表里 ——
    是那 31.4%「系统比例与图上印刷值不一致」的一张。`tile_dpi_for` 照单全收，
    算出 2505 DPI、A0 一页约 1.4 万块瓦片，冒烟导出的 181 块几乎全来自它。

    比例分母不在国标表内就回落兜底 DPI（兜底标准是国标）。
    """
    from scripts.model3d.export_yolo_dataset import tile_dpi_for

    dpi = tile_dpi_for({"scale_m_pt": 0.869697871})       # 1:2465
    assert dpi <= 200, f"不合国标的 1:2465 不该算出 {dpi} DPI"


@pytest.mark.unit
def test_standard_scale_is_still_honoured():
    """国标内的比例照常按比例算 —— 守卫不能误伤 1:150 这种主力比例。"""
    from scripts.model3d.export_yolo_dataset import tile_dpi_for

    assert 140 < tile_dpi_for({"scale_m_pt": 0.052917}) < 170     # 1:150


@pytest.mark.unit
def test_tiles_kept_per_drawing_are_capped(tmp_path, monkeypatch):
    """一张图最多留 `MAX_TILES_PER_DRAWING` 块 —— 否则一张图就能占满数据集。

    与金标准生成器「每图至多 2 格」同一个道理：集中在少数图上的样本，
    量出来的是那几张图，不是整个语料。
    """
    import fitz

    import scripts.model3d.export_yolo_dataset as ex
    from core.model3d.yolo_export import meters_to_page

    for sub in ("images", "labels", "verify"):
        (tmp_path / sub).mkdir()
    monkeypatch.setattr(ex, "OUT", str(tmp_path))
    monkeypatch.setattr(ex, "MAX_TILES_PER_DRAWING", 5)

    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    scale = 0.0353
    # 满页铺柱：高 DPI 下几十块瓦片都有框
    cols = [{"outline": [[x, y], [x + 0.6, y], [x + 0.6, y + 0.6], [x, y + 0.6]]}
            for x in (1.0 + 2.0 * i for i in range(14)) for y in (1.0 + 2.0 * j for j in range(9))]
    fe = types.SimpleNamespace(columns=cols, walls=[], slabs=[], scale=scale,
                               origin_pt=(0.0, 0.0), page_h=595.0)

    n_tiles, _, _ = ex._export_tiles(page, fe, "e" * 36, "cap", 400.0, 0, meters_to_page)
    assert n_tiles <= 5, f"每图上限 5，实得 {n_tiles}"
    assert len(list((tmp_path / "images").glob("*.png"))) == n_tiles
