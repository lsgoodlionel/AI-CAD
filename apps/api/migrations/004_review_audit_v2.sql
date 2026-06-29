-- ============================================================
-- Migration 004: 会审审查引擎 V2 升级
--   （对象识别 + 场景识别 + 问题包 + 文书化输出）
-- ============================================================
-- 依赖：必须先执行 003_review_audit.sql（V1 列与新表）。
-- 可重复执行（IF NOT EXISTS）；向后兼容（新增列全部可空）。
-- 对应契约 V2-6：ai_review_issues 与 review_audit_findings 各补 7 列。

-- ============================================================
-- 1. 扩展 ai_review_issues：V2 结构化字段（旧报告这些列为 NULL）
-- ============================================================

ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS object_name     VARCHAR(64);
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS object_basis    VARCHAR(32);
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS scenario        VARCHAR(16);
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS scenario_reason TEXT;
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS question_pack   JSONB;
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS doc_minutes     JSONB;
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS doc_reply       JSONB;

-- ============================================================
-- 2. 扩展 review_audit_findings：V2 结构化字段
-- ============================================================

ALTER TABLE review_audit_findings ADD COLUMN IF NOT EXISTS object_name     VARCHAR(64);
ALTER TABLE review_audit_findings ADD COLUMN IF NOT EXISTS object_basis    VARCHAR(32);
ALTER TABLE review_audit_findings ADD COLUMN IF NOT EXISTS scenario        VARCHAR(16);
ALTER TABLE review_audit_findings ADD COLUMN IF NOT EXISTS scenario_reason TEXT;
ALTER TABLE review_audit_findings ADD COLUMN IF NOT EXISTS question_pack   JSONB;
ALTER TABLE review_audit_findings ADD COLUMN IF NOT EXISTS doc_minutes     JSONB;
ALTER TABLE review_audit_findings ADD COLUMN IF NOT EXISTS doc_reply       JSONB;
