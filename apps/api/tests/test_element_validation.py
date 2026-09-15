"""没通过金标准的构件类 —— 模型页默认隐藏并标「未验证」。

**依据**（`data/model3d/gold/`）：管线两批 0/58、0/16，设备 0/16 —— 管线描的是
机电图下面建筑底图里的结构线，设备框中的是座椅、家具、单线。
继续调阈值之前，先别让它们以「识别结果」的面目出现在模型里。
"""
from __future__ import annotations

import asyncio

import pytest

from services.element_validation import UNVERIFIED_ELEMENT_KINDS, element_validation


@pytest.mark.unit
def test_pipes_and_equipment_are_unverified_with_reasons():
    payload = element_validation()
    kinds = {item["kind"]: item["reason"] for item in payload["unverified"]}
    assert set(kinds) == {"pipes", "equipment"}
    assert all(reason.strip() for reason in kinds.values())


@pytest.mark.unit
def test_the_list_matches_the_gold_standard():
    """清单是写死的，但要和金标准对得上 —— 某天管线/设备判对了，这里会提醒改清单。"""
    from core.model3d.gold.report import class_reports
    from core.model3d.gold.schema_check import load_gold_files

    files = load_gold_files()
    if not files:
        pytest.skip("金标准数据目录不可达")
    rates = {r.object_class: r for r in class_reports(files) if r.has_rate}
    assert rates["pipes_pipe3"].main_ok == 0
    assert rates["equipment_equip3"].main_ok == 0
    assert set(UNVERIFIED_ELEMENT_KINDS) == {"pipes", "equipment"}


@pytest.mark.unit
def test_model_response_carries_the_validation():
    """接线用例：GET /model 必须带上它 —— 回滚到旧版本的 scene 也要有。"""
    from routers.project_models import get_project_model

    class FakeDb:
        async def fetch_one(self, sql, *args):
            return {"status": "ready", "version": 85, "built_at": None, "error": None,
                    "scene": None, "progress": None, "updated_at": None}

    resp = asyncio.run(get_project_model("p1", db=FakeDb(), current_user={"id": "u"}))
    assert {i["kind"] for i in resp["element_validation"]["unverified"]} == {"pipes", "equipment"}
