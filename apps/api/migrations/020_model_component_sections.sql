-- ============================================================
-- Migration 020: 构件截面表（Phase B 工作块三 B-07）
-- ============================================================
-- 说明：
-- - 编号顺延 019（跨视图 z 恢复标高表）。
-- - 记「构件类型 → 真实截面」：梁高×宽 / 板厚 / 墙厚 / 柱截面 / 管径，
--   替换 element_recognizer 的硬编码默认（梁 0.6 / 板 0.12 / 管 0.1）。
-- - source ∈ section|detail|default；estimated=true 表示回落默认（非实测）。
-- - 依赖：001_initial_schema.sql（projects）
-- ============================================================

CREATE TABLE IF NOT EXISTS model_component_sections (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scope_key           VARCHAR(64) NOT NULL,          -- 单体/范围键
    component_type      VARCHAR(16) NOT NULL,          -- beam|column|slab|wall|pipe
    h_m                 NUMERIC(10, 3),                -- 梁/柱 截面高
    w_m                 NUMERIC(10, 3),                -- 梁/柱 截面宽
    thickness_m         NUMERIC(10, 3),                -- 板/墙 厚
    diameter_m          NUMERIC(10, 3),                -- 管径
    source              VARCHAR(16) NOT NULL DEFAULT 'default',  -- section|detail|default
    confidence          NUMERIC(5, 4) NOT NULL DEFAULT 0.3000,
    estimated           BOOLEAN NOT NULL DEFAULT true,
    evidence_ref        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (project_id, scope_key, component_type),
    CONSTRAINT chk_component_type
        CHECK (component_type IN ('beam', 'column', 'slab', 'wall', 'pipe')),
    CONSTRAINT chk_component_source
        CHECK (source IN ('section', 'detail', 'default'))
);

CREATE INDEX IF NOT EXISTS idx_model_component_sections_project
    ON model_component_sections(project_id, scope_key);

-- ============================================================
-- ROLLBACK（正反向可执行）：
--   DROP INDEX IF EXISTS idx_model_component_sections_project;
--   DROP TABLE IF EXISTS model_component_sections;
-- ============================================================
