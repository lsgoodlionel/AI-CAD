"""AGE 图节点写入的参数绑定 —— 与条文入库同源的 asyncpg 风格缺陷。

**实测**（规范批量导入日志）：

    AGE node for article 6dcbcfa2-… failed: sqlalchemy.sql.elements.TextClause

与 `save_articles_to_db` 同源：`build_age_nodes` 里查条文用的也是
**asyncpg 风格**（`$1` + 位置参数），而本项目用 databases + SQLAlchemy。

后果：**知识图谱引擎拿不到规范数据** —— KG 引擎（四引擎之一）
依赖 AGE 里的 Article 节点做条件合规推理，节点没写进去就等于该引擎空转。

同样被 `except` 吞成 logger.error，导入流程照常报成功。
"""
from __future__ import annotations

import pytest

from services.regulation_importer import build_article_query


@pytest.mark.unit
def test_query_uses_named_binding():
    """**核心用例**:参数是 `:name` + 字典,不是 `$1` + 位置。"""
    sql, params = build_article_query("a1b2c3")
    assert "$1" not in sql
    assert ":article_id" in sql
    assert params == {"article_id": "a1b2c3"}


@pytest.mark.unit
def test_uuid_is_cast_explicitly():
    """**uuid 列要显式 CAST** —— 字符串直接比较会类型错。"""
    sql, _params = build_article_query("a1b2c3")
    assert "CAST(:article_id AS uuid)" in sql


@pytest.mark.unit
def test_selects_fields_needed_by_graph():
    """图节点需要的字段一个不少。"""
    sql, _ = build_article_query("x")
    for field in ("article_no", "obligation_level", "is_mandatory"):
        assert field in sql


# ── 书元数据更新（同源缺陷第三处）──────────────────────────────

@pytest.mark.unit
def test_book_metadata_update_uses_named_binding():
    """**第三处同源缺陷**:书元数据更新也是 `$1` + 位置参数。

    后果:**规范的标准号、版本、生效日期全都没写进去** ——
    而版本比对（判断引用的规范是否失效）正依赖这些字段。
    """
    from services.regulation_importer import build_book_update

    sql, params = build_book_update(
        "b1", {"title": "混凝土结构通用规范", "std_no": "GB 55008-2021"})
    assert "$1" not in sql and "$2" not in sql
    assert ":title" in sql and ":std_no" in sql
    assert "CAST(:book_id AS uuid)" in sql
    assert params["book_id"] == "b1"
    assert params["std_no"] == "GB 55008-2021"


@pytest.mark.unit
def test_empty_metadata_yields_nothing():
    """无字段可更新时返回 None —— 不发空 UPDATE。"""
    from services.regulation_importer import build_book_update

    assert build_book_update("b1", {}) is None
    assert build_book_update("b1", {"unknown_field": "x"}) is None
