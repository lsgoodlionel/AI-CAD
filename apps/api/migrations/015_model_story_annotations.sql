-- ============================================================
-- Migration 015: 模型单体 / 楼层归一化与人工标注
-- ============================================================
-- 说明：
-- - 仓库中 007_* 号段已被 review_audit 占用，且后续 migration 显式依赖；
--   因此本迁移顺延为 015，避免破坏既有执行顺序。
-- - 依赖：001_initial_schema.sql（projects / drawings / users）
-- ============================================================

CREATE TABLE IF NOT EXISTS model_building_units (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    unit_key            VARCHAR(64) NOT NULL,
    display_name        VARCHAR(128) NOT NULL,
    baseline_elevation_m NUMERIC(10, 3) NOT NULL DEFAULT 0,
    source              VARCHAR(32) NOT NULL DEFAULT 'detected',
    confidence          NUMERIC(5, 4) NOT NULL DEFAULT 0.5000,
    candidate_sources   JSONB NOT NULL DEFAULT '[]'::jsonb,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (project_id, unit_key)
);

CREATE TABLE IF NOT EXISTS model_story_levels (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    building_unit_key   VARCHAR(64) NOT NULL,
    story_key           VARCHAR(64) NOT NULL,
    display_name        VARCHAR(128) NOT NULL,
    story_order         INTEGER NOT NULL,
    elevation_m         NUMERIC(10, 3) NOT NULL,
    height_m            NUMERIC(10, 3) NOT NULL,
    source              VARCHAR(32) NOT NULL DEFAULT 'inferred',
    confidence          NUMERIC(5, 4) NOT NULL DEFAULT 0.5000,
    is_manual           BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (project_id, building_unit_key, story_key)
);

CREATE TABLE IF NOT EXISTS drawing_model_annotations (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id               UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    drawing_id               UUID NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
    building_unit_key        VARCHAR(64),
    building_unit_display_name VARCHAR(128),
    story_key                VARCHAR(64),
    story_display_name       VARCHAR(128),
    discipline               VARCHAR(64),
    drawing_type             VARCHAR(64),
    elevation_m              NUMERIC(10, 3),
    scale_text               VARCHAR(64),
    candidate_sources        JSONB NOT NULL DEFAULT '[]'::jsonb,
    include_in_model         BOOLEAN NOT NULL DEFAULT true,
    confidence               NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
    annotated_by             UUID REFERENCES users(id) ON DELETE SET NULL,
    annotation_source        VARCHAR(32) NOT NULL DEFAULT 'manual',
    notes                    TEXT,
    created_at               TIMESTAMPTZ DEFAULT now(),
    updated_at               TIMESTAMPTZ DEFAULT now(),
    UNIQUE (project_id, drawing_id)
);

CREATE TABLE IF NOT EXISTS model_build_issues (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    drawing_id          UUID REFERENCES drawings(id) ON DELETE CASCADE,
    issue_type          VARCHAR(64) NOT NULL,
    severity            VARCHAR(32) NOT NULL DEFAULT 'warning',
    building_unit_key   VARCHAR(64),
    story_key           VARCHAR(64),
    message             TEXT NOT NULL,
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
    resolved            BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_model_building_units_project
    ON model_building_units(project_id, unit_key);
CREATE INDEX IF NOT EXISTS idx_model_story_levels_project
    ON model_story_levels(project_id, building_unit_key, story_order);
CREATE INDEX IF NOT EXISTS idx_drawing_model_annotations_project
    ON drawing_model_annotations(project_id, drawing_id);
CREATE INDEX IF NOT EXISTS idx_model_build_issues_project
    ON model_build_issues(project_id, resolved, severity);
