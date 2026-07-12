-- 025 楼层标高人工录入/校正表
-- 自动识别打底(scene.floors 的 elevation_m/height 为参考);人工在此录入/校正真实层高。
-- 建模时以 source='manual' 作为最高优先级 z_override 覆盖自动值(见 model_builder)。
CREATE TABLE IF NOT EXISTS model_story_manual_heights (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id         UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scope_key          VARCHAR(64) NOT NULL,          -- 单体键（main / A区 …）
    story_key          VARCHAR(64) NOT NULL,          -- 楼层键（F1/B2/RF）
    story_order        INTEGER NOT NULL DEFAULT 0,
    height_m           NUMERIC(10, 3) NOT NULL,       -- 人工录入层高（米）
    elevation_bottom_m NUMERIC(10, 3),                -- 可选:人工录入本层底标高;空则由层高链推算
    note               TEXT,
    updated_by         VARCHAR(64),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, scope_key, story_key),
    CONSTRAINT chk_manual_height_positive CHECK (height_m > 0)
);

CREATE INDEX IF NOT EXISTS idx_model_story_manual_heights_project
    ON model_story_manual_heights(project_id, scope_key, story_order);
