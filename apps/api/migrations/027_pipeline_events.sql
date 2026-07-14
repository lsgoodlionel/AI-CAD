-- ============================================================
-- Migration 027: 事件编排层（Phase D · 泳道3 · D-08）
-- ============================================================
-- 蓝图来源：docs/PHASE_D_BLUEPRINT.md § D-架构-2 / 泳道3 D-08。
-- 目标：轻量事件驱动编排层（Postgres 事件表 + Celery，不引入新中间件），
-- 把散落的模块串起来，实现「自动打底、人工确认」——
--   ai_review.completed → 刷新 rebuild-impact → 超阈值生成「建议重建模型」待办
--   model.built          → 刷新 QTO         → 节约额超阈值生成「建议创建创效提案」待办
--
-- 设计取舍（重要）：
-- 本迁移只新增两张表，不修改 drawings / project_models / model_quantities 等
-- 既有表结构或写入路径（硬约束）。所有自动环节只落「建议/待办」+ 事件审计，
-- 绝不自动执行三审/签字/重建等有副作用的硬动作——采纳与否始终由人工触发。
-- ============================================================

-- ── 事件表：记录管线中发生的关键事件，供 Celery 异步消费 ──────────
CREATE TABLE IF NOT EXISTS pipeline_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type   VARCHAR(100) NOT NULL,   -- 'drawing.uploaded' | 'ai_review.completed' | 'model.built' | ...
    project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_id    UUID,                    -- 触发该事件的来源记录 id（drawing_id / report_id 等，弱引用不加外键）
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    status       VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pipeline_events_project
    ON pipeline_events(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_events_type_status
    ON pipeline_events(event_type, status);

-- ── 建议待办表：编排层生成的「自动打底」建议，人工采纳/忽略 ──────
CREATE TABLE IF NOT EXISTS pipeline_suggestions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    event_id       UUID REFERENCES pipeline_events(id) ON DELETE SET NULL,
    suggestion_type VARCHAR(50) NOT NULL
        CHECK (suggestion_type IN ('rebuild_model', 'create_proposal')),
    status         VARCHAR(20) NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'accepted', 'dismissed')),
    title          VARCHAR(200) NOT NULL,
    summary        TEXT,
    payload        JSONB NOT NULL DEFAULT '{}'::jsonb,   -- 支撑判断的度量（变更图纸数/预估节约额等）
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at    TIMESTAMPTZ,
    resolved_by    UUID REFERENCES users(id)
);

-- 同一项目同一类型建议同时只保留一条「未处理」——重复触发时刷新既有一条，
-- 而不是无限堆积待办列表（幂等；见 core/pipeline/handlers.py _upsert_suggestion）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_pipeline_suggestions_open_per_type
    ON pipeline_suggestions(project_id, suggestion_type)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_pipeline_suggestions_project_status
    ON pipeline_suggestions(project_id, status, created_at DESC);
