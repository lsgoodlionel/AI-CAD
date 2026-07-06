-- ============================================================
-- Migration 012: 套图审查批次（批量 / 整套工程审图编排）
-- ============================================================
-- 蓝图来源：docs/BATCH_REVIEW_BLUEPRINT.md 第 3 节（Phase 5 模块 B）。
-- 依赖：必须先执行 001_initial_schema.sql（projects / users / drawings）。
-- 可重复执行（IF NOT EXISTS）；向后兼容（新表，不改动既有表）。
--
-- summary 结构：
--   {total,done,failed,issues_total,critical_total,
--    by_severity:{critical,major,minor,info},by_discipline:{专业:数量}}
-- cross_findings 结构（core/ai_review/cross_drawing.analyze_batch 输出）：
--   {重复图号,版本冲突,接口缺图,问题聚类,高频对象聚合,严重度分布,专业分布}
-- ============================================================

CREATE TABLE IF NOT EXISTS review_batches (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   UUID NOT NULL REFERENCES projects(id),
    scope        VARCHAR(16) NOT NULL DEFAULT 'multi',     -- single | multi | full_set
    drawing_ids  JSONB NOT NULL DEFAULT '[]',              -- [uuid,...]
    status       VARCHAR(16) NOT NULL DEFAULT 'pending',   -- pending|processing|done|partial_failed|failed
    summary      JSONB,                                    -- 聚合摘要（见文件头注释）
    cross_findings JSONB,                                  -- 跨图分析结果（见文件头注释）
    created_by   UUID REFERENCES users(id),
    created_at   TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_review_batches_project ON review_batches(project_id);
