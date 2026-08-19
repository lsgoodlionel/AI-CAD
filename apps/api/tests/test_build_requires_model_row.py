"""模型记录不存在时，构建必须**报错**而非静默成功。

**实测**：对一个从未建过模的工程直接调 `_do_build`，它跑满 **969 秒**、
算完 16 层轴网与全部构件，然后返回 `{'status': 'ready', 'version': None}`
—— 而数据库里**一条记录都没有**。

根因：`_do_build` 对 `project_models` 的操作全是 `UPDATE`
（`INSERT` 在 API 层 `routers/project_models.py`）。
对不存在的行执行 UPDATE 影响 0 行，`RETURNING version` 返回 None ——
**代码知道出了问题**（后续 `if version is not None` 会跳过实体装配），
**却仍报告 ready**。

任何调用方只要没先建记录，都会拿到一个假的成功，并把 969 秒的
计算结果悄悄丢弃。
"""
from __future__ import annotations

import pytest


@pytest.mark.unit
def test_missing_row_is_a_failure_not_success():
    """**核心用例**:落库影响 0 行 → 判为失败。"""
    from tasks.model_build import build_outcome

    assert build_outcome(version=7)["status"] == "ready"
    assert build_outcome(version=7)["version"] == 7

    failed = build_outcome(version=None)
    assert failed["status"] == "failed"
    assert "project_models" in failed["error"]


@pytest.mark.unit
def test_failure_message_tells_how_to_fix():
    """**错误要说清怎么修** —— 不是「构建失败」四个字。"""
    from tasks.model_build import build_outcome

    error = build_outcome(version=None)["error"]
    assert "记录" in error and ("创建" in error or "INSERT" in error)
