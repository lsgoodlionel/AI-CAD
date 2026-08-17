"""定位口径指标接线单测(placed vs registered 必须分开可见)。"""
from services.model_builder import _build_stats


def _floor(placed=0, registered=0):
    return {"placed_drawings": placed, "registered_drawings": registered,
            "drawings": []}


def test_stats_reports_placed_and_registered_separately():
    """「模型有多少是真定位」不能被「拼上去了」掩盖,两个口径必须分开报。"""
    stats = _build_stats([], {}, [_floor(placed=3, registered=5),
                                 _floor(placed=1, registered=2)], False)
    assert stats["placed_drawings"] == 4
    assert stats["registered_drawings"] == 7


def test_stats_placed_is_zero_when_nothing_world_anchored():
    """没有工程坐标锚点时 placed 必须是 0——不能拿相对配准冒充绝对定位。"""
    stats = _build_stats([], {}, [_floor(placed=0, registered=9)], False)
    assert stats["placed_drawings"] == 0
    assert stats["registered_drawings"] == 9


def test_stats_tolerates_floors_without_the_fields():
    """老模型的 floor 没这两个字段,不该抛错。"""
    stats = _build_stats([], {}, [{"drawings": []}], False)
    assert stats["placed_drawings"] == 0
    assert stats["registered_drawings"] == 0


def test_stats_ignores_non_numeric_values():
    stats = _build_stats([], {}, [{"placed_drawings": None,
                                   "registered_drawings": "", "drawings": []}], False)
    assert stats["placed_drawings"] == 0
