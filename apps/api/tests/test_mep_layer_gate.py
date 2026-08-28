"""机电图层闸：管线/设备不再收下图层已明说是别的东西的几何。

由实测逼出来的 —— 58 个管线候选独立判读 **0 个是管线**（墙 30 / 结构线 21 /
标注线 7），而识别器 `_find_pipes` 当时的唯一判据是「够长且不是轴线」。
柱早已有的「标注层 + 别类层」双闸，管线与设备一直没有。
"""
import pytest

from core.model3d.element_recognizer import _find_equipment, _find_pipes
from core.model3d.types import DrawingGeometry


class _Ctx:
    """最小上下文替身：1 pt = 1 m，坐标原样透传。"""

    src = "d1"

    def len_m(self, v):
        return float(v)

    def to_m(self, x, y):
        return [float(x), float(y)]


def _pipes(layers):
    lines = [(0.0, 0.0, 10.0, 0.0)] * len(layers)
    return _find_pipes(lines, list(layers), set(), "给水平面图", _Ctx())


def test_长线段落在墙图层时不再算管线():
    assert _pipes(["A-WALL"]) == []


def test_长线段落在标注图层时不再算管线():
    assert _pipes(["M-PIPE-TEXT"]) == []


@pytest.mark.parametrize("layer", ["A-WALL", "S-COLUMN", "A-DOOR", "A-WINDOW"])
def test_建筑结构图层一律不算管线(layer):
    assert _pipes([layer]) == []


def test_管线图层上的长线段仍然算管线():
    assert len(_pipes(["M-PIPE-SUPPLY"])) == 1


def test_图层判不出时仍然收下_但这部分尚无真值():
    """57.5% 的线段图层判不出，闸管不到它们 —— 这是已知的能力边界。"""
    assert len(_pipes([""])) == 1


def _equip(poly_layers):
    poly = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
    return _find_equipment([], [], [], [poly] * len(poly_layers),
                           list(poly_layers), [""] * len(poly_layers), [], _Ctx())


def test_柱图层上的方块不再算设备():
    assert _equip(["S-COLUMN"]) == []


def test_墙图层上的方块不再算设备():
    assert _equip(["A-WALL"]) == []


def test_设备图层上的方块仍然算设备():
    assert len(_equip(["M-EQUIPMENT"])) == 1
