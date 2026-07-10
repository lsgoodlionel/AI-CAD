-- ============================================================
-- Migration 016: 模型语义图谱 / 证据 / 图纸分配 / 人工操作审计
-- ============================================================
-- 说明：
-- - 该迁移不把历史 model_building_units 直接确认为真实单体；
--   历史推断仅作为 legacy_inference 候选，等待人工确认或后续自动证据合并。
-- - 语义节点类型保持项目无关，只允许 building_unit / sub_zone /
--   functional_space / construction_zone 四类。
-- ============================================================

CREATE TABLE IF NOT EXISTS model_semantic_nodes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    node_type       VARCHAR(32) NOT NULL CHECK (
        node_type IN ('building_unit', 'sub_zone', 'functional_space', 'construction_zone')
    ),
    canonical_name  VARCHAR(160) NOT NULL,
    normalized_key  VARCHAR(160) NOT NULL,
    parent_id       UUID REFERENCES model_semantic_nodes(id) ON DELETE SET NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'candidate' CHECK (
        status IN ('candidate', 'confirmed', 'rejected', 'merged')
    ),
    confidence      NUMERIC(5, 4) NOT NULL DEFAULT 0.5000,
    source          VARCHAR(32) NOT NULL DEFAULT 'automatic' CHECK (
        source IN ('automatic', 'manual', 'legacy_inference')
    ),
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_model_semantic_active_sibling
    ON model_semantic_nodes(project_id, COALESCE(parent_id, project_id), normalized_key)
    WHERE status IN ('candidate', 'confirmed');

CREATE INDEX IF NOT EXISTS idx_model_semantic_nodes_project
    ON model_semantic_nodes(project_id, node_type, status);
CREATE INDEX IF NOT EXISTS idx_model_semantic_nodes_parent
    ON model_semantic_nodes(parent_id);

CREATE TABLE IF NOT EXISTS model_semantic_evidence (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    node_id         UUID NOT NULL REFERENCES model_semantic_nodes(id) ON DELETE CASCADE,
    drawing_id      UUID REFERENCES drawings(id) ON DELETE CASCADE,
    source          VARCHAR(32) NOT NULL CHECK (
        source IN ('title', 'filename', 'folder_path', 'drawing_no', 'manual', 'legacy_inference')
    ),
    source_value    TEXT NOT NULL,
    location_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence      NUMERIC(5, 4) NOT NULL DEFAULT 0.5000,
    extractor       VARCHAR(64) NOT NULL DEFAULT 'drawing_semantics',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_model_semantic_evidence_node
    ON model_semantic_evidence(node_id);
CREATE INDEX IF NOT EXISTS idx_model_semantic_evidence_drawing
    ON model_semantic_evidence(project_id, drawing_id);

CREATE TABLE IF NOT EXISTS model_semantic_assignments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    drawing_id          UUID NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
    node_id             UUID NOT NULL REFERENCES model_semantic_nodes(id) ON DELETE CASCADE,
    assignment_type     VARCHAR(32) NOT NULL DEFAULT 'semantic_scope' CHECK (
        assignment_type IN ('semantic_scope', 'primary_scope', 'secondary_scope')
    ),
    status              VARCHAR(16) NOT NULL DEFAULT 'candidate' CHECK (
        status IN ('candidate', 'confirmed', 'rejected')
    ),
    confidence          NUMERIC(5, 4) NOT NULL DEFAULT 0.5000,
    source              VARCHAR(32) NOT NULL DEFAULT 'automatic' CHECK (
        source IN ('automatic', 'manual', 'legacy_inference')
    ),
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, drawing_id, node_id, assignment_type)
);

CREATE INDEX IF NOT EXISTS idx_model_semantic_assignments_project
    ON model_semantic_assignments(project_id, status);
CREATE INDEX IF NOT EXISTS idx_model_semantic_assignments_drawing
    ON model_semantic_assignments(project_id, drawing_id);
CREATE INDEX IF NOT EXISTS idx_model_semantic_assignments_node
    ON model_semantic_assignments(node_id);

CREATE TABLE IF NOT EXISTS model_semantic_operations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    operation_type  VARCHAR(32) NOT NULL CHECK (
        operation_type IN ('confirm', 'reject', 'rename', 'merge', 'split', 'reparent', 'assign', 'unassign')
    ),
    node_id         UUID REFERENCES model_semantic_nodes(id) ON DELETE SET NULL,
    target_node_id  UUID REFERENCES model_semantic_nodes(id) ON DELETE SET NULL,
    drawing_id      UUID REFERENCES drawings(id) ON DELETE SET NULL,
    before_state    JSONB NOT NULL DEFAULT '{}'::jsonb,
    after_state     JSONB NOT NULL DEFAULT '{}'::jsonb,
    expected_version INTEGER,
    performed_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_model_semantic_operations_project
    ON model_semantic_operations(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_semantic_operations_node
    ON model_semantic_operations(node_id);

INSERT INTO model_semantic_nodes (
    project_id,
    node_type,
    canonical_name,
    normalized_key,
    status,
    confidence,
    source
)
SELECT
    project_id,
    'building_unit',
    display_name,
    unit_key,
    'candidate',
    confidence,
    'legacy_inference'
FROM model_building_units
ON CONFLICT DO NOTHING;
