-- ============================================================
-- Migration 021: 构件拓扑关系（Phase B 工作块五 B-15）
-- ============================================================
-- 说明：
-- - 编号顺延 020（构件截面表）。
-- - 记 B-12~B-14 拓扑关系：门窗-墙从属 / 梁-柱支承 / 板-梁托承，
--   供 IFC 扣减依据与几何一致性检查（驱动 stable_component_boundaries/geometry_consistent gate）。
-- - 覆盖式落库（每 scope 先删后插），故不设 UNIQUE，仅索引加速查询。
-- - 依赖：001_initial_schema.sql（projects）
-- ============================================================

CREATE TABLE IF NOT EXISTS model_topology_relations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scope_key           VARCHAR(64) NOT NULL,
    relation_type       VARCHAR(16) NOT NULL,          -- host | beam_support | slab_support
    source_id           VARCHAR(64) NOT NULL,
    source_type         VARCHAR(16) NOT NULL,          -- opening|beam|slab
    target_id           VARCHAR(64) NOT NULL,
    target_type         VARCHAR(16) NOT NULL,          -- wall|column|beam
    end_label           VARCHAR(16),                   -- 梁端 start|end（仅 beam_support）
    confidence          NUMERIC(5, 4) NOT NULL DEFAULT 0.5000,
    evidence_ref        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT chk_topology_relation_type
        CHECK (relation_type IN ('host', 'beam_support', 'slab_support'))
);

CREATE INDEX IF NOT EXISTS idx_model_topology_relations_scope
    ON model_topology_relations(project_id, scope_key, relation_type);

-- ============================================================
-- ROLLBACK（正反向可执行）：
--   DROP INDEX IF EXISTS idx_model_topology_relations_scope;
--   DROP TABLE IF EXISTS model_topology_relations;
-- ============================================================
