-- ============================================================
-- Migration 014: 模型构建实时进度
-- ============================================================
-- 依赖：013_project_models.sql。可重复执行；向后兼容（可空列）。
--
-- progress 结构：
--   {stage: fetch|render|recognize|assemble|done,
--    stage_label, current, done, total, updated_at}
-- ============================================================

ALTER TABLE project_models ADD COLUMN IF NOT EXISTS progress JSONB;
