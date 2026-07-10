-- ============================================================
-- Migration 022: 工程量汇总（Phase B 工作块六 B-19）
-- ============================================================
-- 说明：
-- - 编号顺延 021（构件拓扑关系）。
-- - 存项目/单体/楼层级工程量汇总快照：混凝土净/毛体积、模板面积、钢筋质量、估算占比。
--   完整下钻数据存 payload（jsonb）；标量列供快速查询与看板。
-- - 依赖：001_initial_schema.sql（projects）
-- ============================================================

CREATE TABLE IF NOT EXISTS model_quantities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scope_key           VARCHAR(64) NOT NULL,          -- scene | 单体 key
    concrete_net_m3     NUMERIC(14, 4) NOT NULL DEFAULT 0,
    concrete_gross_m3   NUMERIC(14, 4) NOT NULL DEFAULT 0,
    formwork_contact_m2 NUMERIC(14, 4) NOT NULL DEFAULT 0,
    rebar_kg            NUMERIC(14, 2),                -- NULL = 未提供配筋
    estimated_ratio     NUMERIC(5, 4) NOT NULL DEFAULT 0,
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 完整下钻（by_floor/by_building）
    generated_at        TIMESTAMPTZ DEFAULT now(),
    UNIQUE (project_id, scope_key)
);

CREATE INDEX IF NOT EXISTS idx_model_quantities_project
    ON model_quantities(project_id, scope_key);

-- ============================================================
-- ROLLBACK（正反向可执行）：
--   DROP INDEX IF EXISTS idx_model_quantities_project;
--   DROP TABLE IF EXISTS model_quantities;
-- ============================================================
