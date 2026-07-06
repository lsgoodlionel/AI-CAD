-- ============================================================
-- Migration 013: 工程 3D 模型基座（项目级可视化模型）
-- ============================================================
-- 蓝图来源：docs/MODEL_BASE_BLUEPRINT.md 第 3 节（Phase 6 模块 D）。
-- 依赖：必须先执行 001_initial_schema.sql（projects）。
-- 可重复执行（IF NOT EXISTS）；向后兼容（新表，不改动既有表）。
--
-- scene 结构（蓝图第 4 节前后端契约）：
--   {project,floors,markers,cross_links,ifc_models,stats,generated_at}
-- assets 结构：
--   {drawing_id:{image_key,width,height,parser}}
-- ============================================================

CREATE TABLE IF NOT EXISTS project_models (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID NOT NULL UNIQUE REFERENCES projects(id),
    status      VARCHAR(16) NOT NULL DEFAULT 'pending', -- pending|building|ready|failed
    version     INT NOT NULL DEFAULT 0,                 -- 每次重建 +1
    scene       JSONB,
    assets      JSONB,      -- {drawing_id:{image_key,width,height,parser}}
    error       TEXT,
    built_at    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);
