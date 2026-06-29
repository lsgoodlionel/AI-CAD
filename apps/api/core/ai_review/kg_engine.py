"""
知识图谱引擎（Apache AGE）：基于图数据库的规范关联查询。
- 主路径：AGE Cypher → 图谱遍历（规范强制要求 + 关联检查）
- 降级路径：若 AGE 不可用，直接查 regulation_articles 关系表
"""
import logging
import asyncpg

from core.config import settings
from .base import BaseEngine, DrawingContext, AIIssue, IssueSeverity

logger = logging.getLogger(__name__)

# 规范强条 — 发现缺失则升级为 critical
_MANDATORY_REFS = {
    "GB50010-2010", "GB50011-2010", "GB50009-2012",
    "GB50016-2014", "GB50045-95",
}


class KGEngine(BaseEngine):
    engine_name = "kg"

    async def analyze(self, ctx: DrawingContext, db) -> list[AIIssue]:
        issues: list[AIIssue] = []

        # ── 1. 尝试 AGE Cypher 图谱查询 ───────────────────────
        age_issues = await self._query_age(ctx)
        if age_issues:
            issues.extend(age_issues)
            logger.info("[KGEngine] AGE 返回 %d 条图谱结论", len(age_issues))
        else:
            # ── 2. 降级：直接查 regulation_articles 表 ────────
            sql_issues = await self._query_articles(ctx, db)
            issues.extend(sql_issues)
            logger.info("[KGEngine] SQL 降级返回 %d 条规范参考", len(sql_issues))

        return issues

    # ──────────────────────── AGE 路径 ────────────────────────

    async def _query_age(self, ctx: DrawingContext) -> list[AIIssue]:
        try:
            conn = await asyncpg.connect(settings.database_url)
            try:
                await conn.execute("LOAD 'age'")
                await conn.execute("SET search_path = ag_catalog, \"$user\", public")

                rows = await conn.fetch(
                    """
                    SELECT * FROM cypher('regulation_graph', $$
                        MATCH (d:Discipline {code: $discipline})-[:REQUIRES]->(s:Standard)
                        OPTIONAL MATCH (s)-[:HAS_CLAUSE]->(c:Clause {mandatory: true})
                        RETURN s.code AS std_code,
                               s.name AS std_name,
                               c.article_no AS article_no,
                               c.content    AS content,
                               c.mandatory  AS mandatory
                        LIMIT 20
                    $$, $params) AS t(std_code agtype, std_name agtype,
                                       article_no agtype, content agtype, mandatory agtype)
                    """,
                    {"discipline": ctx.discipline},
                )
            finally:
                await conn.close()

            issues: list[AIIssue] = []
            for row in rows:
                std_code = str(row["std_code"]).strip('"') if row["std_code"] else ""
                article = str(row["article_no"]).strip('"') if row["article_no"] else ""
                content = str(row["content"]).strip('"') if row["content"] else ""
                mandatory = str(row["mandatory"]).strip('"').lower() == "true"

                severity = IssueSeverity.CRITICAL if mandatory and std_code in _MANDATORY_REFS \
                    else IssueSeverity.INFO

                issues.append(AIIssue(
                    engine=self.engine_name,
                    severity=severity,
                    description=f"图谱关联规范：{content}" if content else f"适用规范：{std_code}",
                    category="知识图谱",
                    regulation_ref=f"{std_code} {article}".strip(),
                ))
            return issues

        except Exception as e:
            logger.debug("[KGEngine] AGE 查询失败，降级至 SQL: %s", e)
            return []

    # ──────────────────────── SQL 降级路径 ───────────────────

    async def _query_articles(self, ctx: DrawingContext, db) -> list[AIIssue]:
        try:
            rows = await db.fetch_all(
                """
                SELECT ra.article_no, ra.title, rb.std_no AS standard_code, ra.content
                FROM regulation_articles ra
                JOIN regulation_books rb ON ra.book_id = rb.id
                WHERE rb.discipline = :discipline
                   OR rb.discipline = 'general'
                   OR rb.discipline IS NULL
                   OR ra.is_mandatory = true
                ORDER BY rb.std_no, ra.article_no
                LIMIT 30
                """,
                {"discipline": ctx.discipline},
            )
        except Exception as e:
            logger.error("[KGEngine] SQL 查询失败: %s", e)
            return []

        issues: list[AIIssue] = []
        for row in rows:
            std_code = row["standard_code"] or ""
            severity = IssueSeverity.INFO
            if any(ref in std_code for ref in _MANDATORY_REFS):
                severity = IssueSeverity.MAJOR

            issues.append(AIIssue(
                engine=self.engine_name,
                severity=severity,
                description=f"适用规范：{row['title']}",
                category="规范关联",
                regulation_ref=f"{std_code} {row['article_no']}".strip(),
            ))
        return issues
