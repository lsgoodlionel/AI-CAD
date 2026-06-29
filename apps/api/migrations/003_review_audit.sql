-- ============================================================
-- Migration 003: 图纸会审审图升级（会审经验蒸馏协议）
-- ============================================================
-- 可重复执行（IF NOT EXISTS）；向后兼容（新增列全部可空）

-- ── 扩展 ─────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()

-- ============================================================
-- 1. 扩展 ai_review_issues：会审结构化字段（旧报告这些列为 NULL）
-- ============================================================

ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS discipline_code   VARCHAR(8);
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS location_json     JSONB;
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS concerns          JSONB;
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS issue_class       JSONB;
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS interface_primary VARCHAR(32);
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS interface_related JSONB;
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS risk_level        VARCHAR(8);
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS object_level      VARCHAR(16);
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS standard_question TEXT;
ALTER TABLE ai_review_issues ADD COLUMN IF NOT EXISTS evidence_gap      JSONB;

-- ============================================================
-- 2. 独立会审记录（纯文本会审输入，可不关联图纸文件）
-- ============================================================

CREATE TABLE IF NOT EXISTS review_audit_records (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id),     -- 可空：纯文本会审无需关联项目
    discipline_code VARCHAR(8),
    title           VARCHAR(500) NOT NULL,
    body            TEXT NOT NULL,
    doc_type        VARCHAR(32),
    source_db       VARCHAR(32),
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_review_records_project ON review_audit_records (project_id);
CREATE INDEX IF NOT EXISTS idx_review_records_created ON review_audit_records (created_at DESC);

-- ============================================================
-- 3. 会审结论（结构化审查结果，契约第2节全量字段）
-- ============================================================

CREATE TABLE IF NOT EXISTS review_audit_findings (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id         UUID NOT NULL REFERENCES review_audit_records(id) ON DELETE CASCADE,
    discipline_code   VARCHAR(8),
    discipline_name   VARCHAR(64),
    location_json     JSONB,
    concerns          JSONB,
    issue_class       JSONB,
    interface_primary VARCHAR(32),
    interface_related JSONB,
    risk_level        VARCHAR(8),
    object_level      VARCHAR(16),
    standard_question TEXT,
    evidence_gap      JSONB,
    raw_output        JSONB,            -- audit_text 完整 data 结构快照
    created_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_review_findings_record ON review_audit_findings (record_id);
