-- Migration 003: 经济测算结果存储
-- 每张图纸保留一份最新计算结果（upsert by drawing_id）

CREATE TABLE IF NOT EXISTS drawing_economic_calcs (
    drawing_id   UUID PRIMARY KEY REFERENCES drawings(id) ON DELETE CASCADE,
    calc_input   JSONB        NOT NULL,   -- 原始请求参数（bars/concrete_grade 等）
    calc_result  JSONB        NOT NULL,   -- 计算结果（锚固长度/切割方案/汇总）
    created_by   UUID         REFERENCES users(id),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_economic_calcs_created_at
    ON drawing_economic_calcs (created_at DESC);
