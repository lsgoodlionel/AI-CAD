"""每层参与构件识别的图纸配额。

**实测**：轨道交通全项目 443 张平面图，只用了 77 张（**17%**）——
每层有 21~40 张平面图，而配额只让进 9 张
（structure 2 + beam 2 + mep 3 + architecture 2）。

配额是**构建时长与覆盖面的取舍**，不是缺陷：线程池 `max_workers=2`，
单图识别 10~40 秒，443 张全跑约 90 分钟。
所以它必须**可配置**——写死成魔数，调一次就要改代码、重跑测试、重新部署。
"""
import importlib

import pytest


@pytest.mark.unit
def test_quota_defaults_cover_a_real_floor():
    """默认值要够覆盖一层的主要专业平面图。

    实测一层的平面图按专业分布：mep 最多（全项目 313 张），
    其次 architecture / structure 各 49、decoration 32。
    """
    from services import model_elements

    assert model_elements._MAX_MEP_PLANS >= 6
    assert model_elements._MAX_STRUCTURE_PLANS >= 4
    assert model_elements._MAX_ARCHITECTURE_PLANS >= 4
    assert model_elements._MAX_BEAM_PLANS >= 3


@pytest.mark.unit
def test_quota_is_overridable_by_env(monkeypatch):
    """**调配额不该需要改代码**——它是运维参数，不是业务常量。"""
    monkeypatch.setenv("CAD_MAX_STRUCTURE_PLANS", "11")
    monkeypatch.setenv("CAD_MAX_MEP_PLANS", "13")
    from services import model_elements

    importlib.reload(model_elements)
    try:
        assert model_elements._MAX_STRUCTURE_PLANS == 11
        assert model_elements._MAX_MEP_PLANS == 13
    finally:
        monkeypatch.undo()
        importlib.reload(model_elements)


@pytest.mark.unit
def test_invalid_env_falls_back_to_default(monkeypatch):
    """环境变量写错不能让建模崩掉——**判不出就用默认值**，
    而且要记下来，否则「我明明配了」会变成一个谜。"""
    monkeypatch.setenv("CAD_MAX_MEP_PLANS", "很多")
    from services import model_elements

    importlib.reload(model_elements)
    try:
        assert model_elements._MAX_MEP_PLANS >= 6
    finally:
        monkeypatch.undo()
        importlib.reload(model_elements)


@pytest.mark.unit
def test_zero_or_negative_quota_is_rejected(monkeypatch):
    """配额 0 会让整层没有构件——这多半是误配，不是意图。"""
    monkeypatch.setenv("CAD_MAX_STRUCTURE_PLANS", "0")
    from services import model_elements

    importlib.reload(model_elements)
    try:
        assert model_elements._MAX_STRUCTURE_PLANS >= 4
    finally:
        monkeypatch.undo()
        importlib.reload(model_elements)
