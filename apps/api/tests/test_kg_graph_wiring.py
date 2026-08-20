"""KG 图谱通路：写入端与读取端必须落在同一张图、同一套标签。

**背景**——实测发现 KG 引擎（四引擎之一）的 AGE 图谱推理
**从未执行过一次**，四层脱节各自独立、任意一层都足以让它归零：

1. 读取端 `cypher(…, $params)` —— `$params` 不是合法占位符，
   查询必然抛 `PostgresSyntaxError`；
2. 读取端 MATCH 的 `Discipline/Standard/Clause` 三类节点
   **全仓没有任何代码创建过**；
3. 写入端写 `cad_graph`，读取端查 `regulation_graph`；
4. 写入端参数绑定是 asyncpg 风格。

而 `if age_issues: … else: SQL 降级` 让整条失效路径完全无声——
日志只说「SQL 降级返回 N 条」，读的人会以为那是正常的次要路径。
"""
import json

import pytest


BOOK = {"id": "b1", "std_no": "GB 55008-2021",
        "title": "混凝土结构通用规范", "discipline": "structure"}
ARTICLE = {"id": "a1", "article_no": "4.1.1",
           "content": "混凝土强度等级不应低于C25",
           "is_mandatory": True, "obligation_level": "MUST"}


def _joined(book=None, article=None) -> str:
    from services.regulation_importer import build_clause_statements
    return " ".join(build_clause_statements(book or BOOK, article or ARTICLE))


# ── 写入端 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_write_targets_same_graph_as_reader():
    """写入端与读取端图名必须一致——否则节点写进平行宇宙。"""
    from services.regulation_importer import GRAPH_NAME
    from core.ai_review.kg_engine import KG_GRAPH_NAME

    assert GRAPH_NAME == KG_GRAPH_NAME
    cypher = _joined()
    assert f"cypher('{GRAPH_NAME}'" in cypher
    assert "cad_graph" not in cypher


@pytest.mark.unit
def test_write_builds_labels_the_reader_matches():
    """写入端建的标签/关系，必须正是读取端 MATCH 的那套。

    此前写入端只建扁平的 `(a:Article)` —— 名字都对不上。
    """
    cypher = _joined()
    for token in (":Discipline", ":Standard", ":Clause",
                  ":REQUIRES", ":HAS_CLAUSE"):
        assert token in cypher, f"缺 {token}"


@pytest.mark.unit
def test_cross_node_set_is_split_into_separate_statements():
    """**AGE 1.5.0 实测**：一条语句里只有第一个 MERGE 绑定的变量能被 SET，
    其余节点的属性赋值被**静默丢弃**（不报错、不警告），
    换赋值顺序也没用——决定权在 MERGE 顺序。

    所以跨节点属性必须拆成多条语句。这条只有对真库实跑才暴露：
    合成的语句字符串完全合法，比字符串的单测看不出来。
    """
    from services.regulation_importer import build_clause_statements

    statements = build_clause_statements(BOOK, ARTICLE)
    for statement in statements:
        set_targets = {piece.split(".")[0].strip()
                       for piece in statement.split("SET ")[1:]}
        assert len(set_targets) <= 1, f"一条语句 SET 了多个节点：{statement}"


@pytest.mark.unit
def test_clause_statement_index_points_at_the_clause():
    """`age_node_id` 存的是**条文**节点。取错下标会把整本书的条文
    全指到同一个 Standard 节点上——而且看起来一切正常。"""
    from services.regulation_importer import (
        CLAUSE_STATEMENT_INDEX, build_clause_statements)

    statements = build_clause_statements(BOOK, ARTICLE)
    assert ":Clause" in statements[CLAUSE_STATEMENT_INDEX]
    assert ":Standard" not in statements[CLAUSE_STATEMENT_INDEX]
    assert "RETURN id(c)" in statements[CLAUSE_STATEMENT_INDEX]


@pytest.mark.unit
def test_mandatory_is_boolean_literal_not_string():
    """读取端按 `{mandatory: true}` 过滤——写成字符串 'true' 就永不命中。"""
    cypher_yes = _joined()
    cypher_no = _joined(article={**ARTICLE, "is_mandatory": False,
                                 "obligation_level": "SHOULD"})
    assert "c.mandatory = true" in cypher_yes
    assert "c.mandatory = 'true'" not in cypher_yes
    assert "c.mandatory = false" in cypher_no


@pytest.mark.unit
def test_quotes_in_regulation_text_are_escaped():
    """规范标题含书名号、条文含引号是常态——不转义会把 cypher 打断。

    转义规约是 **cypher 的反斜杠**，不是 SQL 的双写单引号：
    后者会原样传进 cypher 解析器报 `syntax error at or near "'C25'"`。
    """
    cypher = _joined(
        book={**BOOK, "title": "混凝土'结构'通用规范"},
        article={**ARTICLE, "content": "应符合'本规范'第4章 RETURN 1 //"})
    assert cypher.count("\\'") >= 3
    assert "''" not in cypher


@pytest.mark.unit
def test_missing_discipline_still_writes_standard_and_clause():
    """规范书没标专业是常见的（解析不出）——不能因此整条不写，
    但也不能凭空编一个专业归属。"""
    cypher = _joined(book={**BOOK, "discipline": None})
    assert ":Standard" in cypher and ":Clause" in cypher
    assert ":Discipline" not in cypher


# ── 读取端 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_reader_query_has_no_invalid_placeholder():
    """`$params` 不是 asyncpg 占位符——实测直接 PostgresSyntaxError。"""
    from core.ai_review.kg_engine import build_kg_query

    sql, params = build_kg_query("structure")
    assert "$params" not in sql
    assert "$1::agtype" in sql          # AGE 第三参数须是 agtype
    assert json.loads(params[0]) == {"discipline": "structure"}


@pytest.mark.unit
def test_reader_matches_what_writer_builds():
    """读写两端的标签集合必须重合——这是本文件存在的全部理由。"""
    from core.ai_review.kg_engine import build_kg_query

    sql, _ = build_kg_query("structure")
    cypher = _joined()
    for label in (":Discipline", ":Standard", ":Clause",
                  ":REQUIRES", ":HAS_CLAUSE"):
        assert label in sql and label in cypher, f"{label} 两端不一致"
