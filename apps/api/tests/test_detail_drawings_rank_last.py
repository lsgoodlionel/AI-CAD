"""详图不该抢在平面图前面出构件（J7 收尾）。

**实测**：`S-1-32-201C 一~三层柱配筋详图`（比例 **1:25**）与
`S-1-32-103C 三~四层柱平面图`（1:150）都被选进构件识别。
但详图画的是**配筋大样**，它的「柱」不是平面位置 ——
识别成功反而会往模型里塞 22 根坐标错误的柱。

它此前因超时被跳过，是**歪打正着**；超时提到 60 秒后就该正面处理。

**不能硬排除**：用户约束「预留没有整套图纸的部分图纸建模功能」——
某层只有详图时，用详图总比没有强。所以是**排到最后**，不是删掉。
"""
from __future__ import annotations

import pytest

from services.model_elements import _transform_rank


def _d(did: str, title: str) -> dict:
    return {"id": did, "title": title}


@pytest.mark.unit
def test_detail_ranks_after_a_plan():
    """**核心用例**：同层有平面图时，详图不该被选中。"""
    plan = _d("a", "结构-竣工图--南区三~四层柱平面图")
    detail = _d("b", "结构-竣工图--南区一~三层柱配筋详图")
    assert _transform_rank(plan, None) < _transform_rank(detail, None)


@pytest.mark.unit
def test_detail_is_still_usable_when_nothing_else_exists():
    """**部分图纸建模**：只有详图时它仍是有限的可用信息，不能判成不可用。"""
    detail = _d("b", "结构-竣工图--南区一~三层柱配筋详图")
    assert _transform_rank(detail, None) < 10_000, "排后面，但不是排除"


@pytest.mark.unit
def test_plan_with_world_placement_still_wins():
    """详图降级不得打乱既有优先级：世界摆放仍排最前。"""
    plan = _d("a", "结构-竣工图--三~四层柱平面图")
    placements = {"a": {"scale": 1.0, "rotation_deg": 0.0, "tx": 0.0, "ty": 0.0}}
    assert _transform_rank(plan, None, placements) < _transform_rank(plan, None)


@pytest.mark.unit
def test_unknown_title_is_not_treated_as_detail():
    """判不出就按常规图处理 —— 不能因为看不懂图名就把它压到最后。"""
    plain = _d("c", "")
    detail = _d("b", "柱配筋详图")
    assert _transform_rank(plain, None) < _transform_rank(detail, None)
