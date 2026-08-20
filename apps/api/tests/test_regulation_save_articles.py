"""条文入库用 databases 风格绑定 —— 此前是 asyncpg 风格，从未真正执行过。

**实测**：批量导入强制性通用规范时，每条都报

    save_article 7 failed: TextClause.bindparams() argument after ** must be a mapping

根因：`save_articles_to_db` 用 **asyncpg 风格**（`$1` 占位 + 位置参数），
而本项目用的是 **databases + SQLAlchemy**（`:name` 占位 + 字典参数）。

**这条路径从未被真正执行过** —— 规范库一直是空的（books 0 / articles 0），
所以没人发现。第一本规范 OCR 出 53 条，入库 **0 条**，
而错误被 `except` 吞成 logger.error，导入仍报「成功」。
"""
from __future__ import annotations

import pytest

from services.regulation_importer import build_article_params


@pytest.mark.unit
def test_params_are_a_mapping_not_positional():
    """**核心用例**:参数必须是字典(databases 风格)。"""
    params = build_article_params(
        book_id="b1",
        article={"article_no": "3.1.1", "title": "水泥",
                 "raw_text": "水泥主要控制指标应包括…",
                 "obligation_level": "MUST", "is_mandatory": True,
                 "conditions": [{"if": "抗渗"}]},
    )
    assert isinstance(params, dict)
    assert params["book_id"] == "b1"
    assert params["article_no"] == "3.1.1"
    assert params["is_mandatory"] is True


@pytest.mark.unit
def test_conditions_serialised_as_json_text():
    """`conditions` 是 jsonb 列 —— 必须序列化,不能直接传 list。"""
    params = build_article_params(
        book_id="b1",
        article={"article_no": "1.1", "raw_text": "x",
                 "conditions": [{"if": "A", "then": "B"}]},
    )
    assert isinstance(params["conditions"], str)
    assert '"if"' in params["conditions"]


@pytest.mark.unit
def test_defaults_when_fields_missing():
    """**缺字段给合理默认** —— 分类失败的条文也要能入库留档。"""
    params = build_article_params(book_id="b1",
                                  article={"article_no": "2.1", "raw_text": "y"})
    assert params["obligation_level"] == "SHOULD"
    assert params["is_mandatory"] is False
    assert params["title"] is None
    assert params["conditions"] == "[]"


@pytest.mark.unit
def test_empty_content_is_rejected():
    """**空正文不入库** —— 那是切分噪声。"""
    assert build_article_params(book_id="b1",
                                article={"article_no": "1", "raw_text": ""}) is None
    assert build_article_params(book_id="b1", article={}) is None
