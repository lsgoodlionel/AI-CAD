"""图层分类配置的**单一真相源**：原 layer_class_map.yaml 已并入 layer_conventions.yaml。

背景见 docs/PROGRESS.md「2026-09-14 · 图层分类器批次」：仓库里曾有两份互相分岔
的图层配置 ——

    classify_by_layer（生产唯一判据，管线/设备/柱的图层闸全靠它）
        → data/layer_conventions.yaml        「灯具」出现 0 次
    auto_label（只用于 Phase C 数据集标注）
        → data/model3d/layer_class_map.yaml  「灯具」出现 4 次

装修/吊顶/饰面 的整套词汇仓库里有，却到不了生产路径，歌剧院的装修图层因此
全部落进「图层判不出」那 57.5%。本文件把「两份不再分岔」钉成断言。
"""
from __future__ import annotations

from importlib import import_module

import core.model3d.layer_conventions as lc
from core.model3d.dataset import auto_label as auto_label_fn
from core.model3d.preprocess.schema import Primitive, PrimitiveDoc

#: ``dataset/__init__`` 把同名函数 ``auto_label`` 导出到包命名空间，
#: 于是 ``import ...dataset.auto_label as al`` 拿到的是**函数**而非模块。
al = import_module("core.model3d.dataset.auto_label")


# ── 单一真相源 ───────────────────────────────────────────────────
def test_补充映射表文件已删除只剩一份配置() -> None:
    # Arrange / Act
    legacy = lc._CONVENTIONS_FILE.parent / "model3d" / "layer_class_map.yaml"

    # Assert
    assert lc._CONVENTIONS_FILE.exists()
    assert not legacy.exists(), "两份配置必须合并为一份，勿保留 layer_class_map.yaml"


def test_auto_label_默认映射与生产判据读同一个文件() -> None:
    assert al._MAP_FILE == lc._CONVENTIONS_FILE


def test_auto_label_与生产判据在真实图层名上不再分岔() -> None:
    # Arrange: 覆盖结构/机电/装修/AIA/中文各族
    layers = (
        "S-COLU", "S-BEAM", "S-SLAB", "S-WALL", "A-DOOR", "A-GLAZ", "S-GRID",
        "M-PIPE", "M-EQPM", "I—平顶—灯具", "吊顶造型", "背景墙", "暗柱", "防火门",
        "喷淋支管", "立柱桩", "C-SHET-TTLB", "RANDOM-XYZ",
    )
    doc = PrimitiveDoc(primitives=tuple(
        Primitive(id=i, type="line", points=((0.0, 0.0), (1.0, 1.0)), layer=lay, block="")
        for i, lay in enumerate(layers)))

    # Act
    labeled = auto_label_fn(doc).labeled

    # Assert：弱标注结果必须就是生产判据的结果，一个不差
    for lay, lp in zip(layers, labeled):
        assert lp.category == lc.classify_by_layer(lay), lay


# ── 装修词汇终于到达生产判据 ─────────────────────────────────────
def test_装修灯具图层不再判不出() -> None:
    # 实测歌剧院图层名（全角破折号，非 ASCII 连字符）
    assert lc.classify_by_layer("I—平顶—灯具") == "equipment"


def test_原补充映射的词汇在生产判据上可用() -> None:
    cases = {
        "暗柱": "column",        # 边缘构件
        "YBZ1": "column",  # 11G101 编号形式：代号后紧跟数字
        "圈梁": "beam",
        "防火门": "door",
        "飘窗": "window",
        "背景墙": "wall",
        "成品门": "door",
        "喷淋支管": "pipe",
        "插座": "equipment",
        "烟感": "equipment",
        "附加轴": "axis",
    }
    for layer, expected in cases.items():
        assert lc.classify_by_layer(layer) == expected, layer


def test_吊顶天花刻意不并入不判成板() -> None:
    """两份配置里只有旧 map 把吊顶/天花判成 slab，合并时**刻意不采纳**。

    `slab` 在 services/model_qto.py 里直接变成 `IfcSlab`，按 thickness 出
    混凝土净体积/模板/钢筋，并经 `/model/quantities/to-proposal` 进创效提案。
    吊顶是石膏板不是混凝土 —— 实测并入后 134 张样本 slabs 154→423（+174.7%），
    269 个全部来自 3 张吊顶图。9 类里没有 ceiling，宁可判不出也不判错。
    """
    for layer in ("吊顶", "天花", "顶棚", "A-CLNG", "A-CLNG-ACCS", "地面铺装"):
        assert lc.classify_by_layer(layer) != "slab", layer
    # 「石膏板吊顶」仍会因通用子串「板」判成 slab —— 那是合并前就有的行为，
    # 不是本次并入带进来的，故不在此处断言（改它属另一件事）。


def test_桥架归管线而非设备() -> None:
    """两份配置对「桥架」判得不一样（旧 conventions=设备 / 旧 map=管线）。

    合并必须**做选择**而不是靠 _KIND_ORDER 碰运气：电缆桥架是线性敷设，
    `_find_pipes` 走线段、`_find_equipment` 走矩形/多边形，桥架属前者。
    """
    assert lc.classify_by_layer("桥架") == "pipe"
    assert lc.classify_by_layer("E-桥架-1F") == "pipe"


# ── 合并不得撤销已修过的两处学科代码碰撞 ─────────────────────────
def test_合并后_C_前缀仍未撞_Civil() -> None:
    # C = Civil 学科代码；图框标题块不是窗
    assert lc.classify_by_layer("C-SHET-TTLB") != "window"
    # 中国院窗编号 C+数字 信息不丢
    assert lc.classify_by_layer("A-ANNO", block="C-1815") == "window"
    assert lc.classify_by_layer("C1") == "window"


def test_合并后_M_前缀仍未撞_Mechanical() -> None:
    # M = Mechanical 学科代码；整个机电命名空间不得被判成门
    assert lc.classify_by_layer("M-PIPE") == "pipe"
    assert lc.classify_by_layer("M-PIPE-SUPPLY") == "pipe"
    assert lc.classify_by_layer("M-EQPM") == "equipment"
    assert lc.classify_by_layer("M-DUCT") == "pipe"
    # 中国院门编号 M+数字 信息不丢
    assert lc.classify_by_layer("0", block="M-1521") == "door"
    assert lc.classify_by_layer("M1") == "door"
