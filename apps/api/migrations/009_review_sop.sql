-- ============================================================
-- Migration 009: 会审审查引擎 V3 升级
--   （SOP 逐项清单核查：审图目标 + 未来影响 + 清单覆盖率）
-- ============================================================
-- 知识来源：06_认知蒸馏/05_专业审图清单SOP.md（19 专业 × 1909 条会审记录蒸馏）。
-- 依赖：必须先执行 007_review_audit.sql、008_review_audit_v2.sql。
-- 可重复执行（IF NOT EXISTS）；向后兼容（新增列可空，旧报告该列为 NULL）。
--
-- review_sop 结构：
--   {protected_result, why_now,
--    future_impact:{stage, effect},
--    checklist:{ratio, checked, covered, items:[...], uncovered:[...]}}
-- ============================================================

ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS review_sop JSONB;
