-- ============================================================
-- Migration 019: 跨视图 z 恢复 — 真实标高表（Phase B 工作块二 B-03）
-- ============================================================
-- 说明：
-- - 编号顺延：Phase A 已占用 017/018，本迁移为 019。
-- - 与 015 的 model_story_levels（归一化楼层表：elevation_m/height_m/is_manual）
--   概念不同——本表专记「跨视图恢复」的溯源证据：来源(section/elevation/estimated)、
--   evidence_ref（证据链：源图/图元/拟合残差），故独立命名 model_z_recovery_levels，
--   避免与 015 字段/语义重叠（对齐 B-03 风险注记）。
-- - 依赖：001_initial_schema.sql（projects）
-- ============================================================

CREATE TABLE IF NOT EXISTS model_z_recovery_levels (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scope_key           VARCHAR(64) NOT NULL,          -- 单体/范围键（沿用 building_unit_key）
    story_key           VARCHAR(64) NOT NULL,          -- 楼层键（F1/B2/RF）
    story_order         INTEGER NOT NULL DEFAULT 0,
    elevation_bottom_m  NUMERIC(10, 3) NOT NULL,       -- 本层底真实标高（±0.000 基准，米）
    story_height_m      NUMERIC(10, 3) NOT NULL,       -- 本层层高（米）
    source              VARCHAR(16) NOT NULL,          -- section | elevation | estimated
    confidence          NUMERIC(5, 4) NOT NULL DEFAULT 0.5000,
    evidence_ref        JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 证据链：源图/图元/拟合残差
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (project_id, scope_key, story_key),
    CONSTRAINT chk_z_recovery_source
        CHECK (source IN ('section', 'elevation', 'estimated'))
);

CREATE INDEX IF NOT EXISTS idx_model_z_recovery_levels_project
    ON model_z_recovery_levels(project_id, scope_key, story_order);

-- ============================================================
-- ROLLBACK（正反向可执行）：
--   DROP INDEX IF EXISTS idx_model_z_recovery_levels_project;
--   DROP TABLE IF EXISTS model_z_recovery_levels;
-- ============================================================
