-- ============================================================
-- Migration 023: 符号 spotting 推理引擎种子 + 调用日志表（Phase C · C-12）
-- ============================================================
-- 目标：把符号 spotting（CV 学习模型，非 LLM chat）纳入 ModelRouter 引擎治理体系。
--   1) 种子 `symbol_spotting` 引擎的 engine_model_configs（primary/fallback），
--      范式对齐 018_vlm_engine_seed.sql，使其可在管理后台「引擎配置」统一治理、
--      锁定配置漂移（primary=CADTransformer 权重后端；fallback_1=离线 mock 兜底）。
--   2) 新增 spotting 专用调用日志表 symbol_spotting_logs。
--
-- 为何用**专用日志表**而非复用 llm_call_logs：
--   spotting 是 CV 模型，关键指标是 backend / candidate_count / latency，与 LLM 的
--   prompt/completion token、cost_usd 列语义不符；硬塞进 token 列会污染 LLM 成本看板。
--   故引擎**配置治理复用** engine_model_configs（与 ModelRouter 同一张表），而调用
--   **日志落专用表**，各取所需、语义清晰。
--
-- 依赖：002_model_management.sql（llm_providers / llm_models / engine_model_configs）。
-- 幂等：全部使用 ON CONFLICT DO NOTHING / IF NOT EXISTS，可重复执行。
-- 执行：psql "$DATABASE_URL" -f apps/api/migrations/023_symbol_spotting.sql
-- ============================================================

-- ── §1 提供商：本地符号识别服务（CADTransformer/VecFormer 推理后端）──────────
-- provider_type 用 custom_http（自研/专业模型 REST API，见 CLAUDE.md 模型路由）。
-- 无 API Key（本地推理），base_url 为占位；生产按 infra/k8s 实际推理服务地址覆盖。
INSERT INTO llm_providers (name, provider_type, base_url, api_key_env)
VALUES (
    '本地符号识别服务',
    'custom_http',
    'http://cad-spotting:8500',
    NULL
)
ON CONFLICT (name) DO NOTHING;

-- ── §2 模型：CADTransformer 权重后端 + 离线 mock 桩 ───────────────────────────
-- UNIQUE(provider_id, model_id) 保证幂等。本地推理价格恒 0。
-- supports_vision=true：spotting 吃图元/SVG 视觉输入（语义上属视觉模型）。
INSERT INTO llm_models
    (provider_id, model_id, display_name, context_window, supports_vision,
     input_price_per_1m, output_price_per_1m)
SELECT p.id, m.model_id, m.display_name, m.ctx, m.vision, m.inp, m.out
FROM llm_providers p
CROSS JOIN LATERAL (VALUES
    ('本地符号识别服务', 'cadtransformer-floorplan', 'CADTransformer 符号识别 (MIT)', 0, true, 0.00, 0.00),
    ('本地符号识别服务', 'mock-spotting',            '离线 Mock 符号识别 (CI 兜底)', 0, true, 0.00, 0.00)
) AS m(pname, model_id, display_name, ctx, vision, inp, out)
WHERE p.name = m.pname
ON CONFLICT (provider_id, model_id) DO NOTHING;

-- ── §3 引擎配置：symbol_spotting（primary / fallback_1）───────────────────────
-- UNIQUE(engine_name, task_type) 保证幂等。temperature/top_p 等 LLM 参数对 CV 无意义，
-- 取占位默认值（不参与推理，仅为满足统一引擎配置表结构；服务不读这些字段做 spotting）。
--   · primary    = CADTransformer 权重后端（有 GPU/权重时启用）
--   · fallback_1 = 离线 Mock（无 GPU 的 CI / 权重未就绪时兜底，保证服务永不硬失败）
INSERT INTO engine_model_configs
    (engine_name, task_type, model_id, temperature, max_tokens, top_p)
SELECT 'symbol_spotting', cfg.task_type, lm.id, 0.00, 0, 1.00
FROM (VALUES
    ('primary',    '本地符号识别服务', 'cadtransformer-floorplan'),
    ('fallback_1', '本地符号识别服务', 'mock-spotting')
) AS cfg(task_type, pname, model_id)
JOIN llm_providers lp ON lp.name = cfg.pname
JOIN llm_models    lm ON lm.provider_id = lp.id AND lm.model_id = cfg.model_id
ON CONFLICT (engine_name, task_type) DO NOTHING;

-- ── §4 调用日志表：symbol_spotting_logs ──────────────────────────────────────
-- 仿 llm_call_logs 的引擎日志范式，但列面向 CV spotting（backend / candidate_count）。
-- 由 core/model3d/spotting/service.py:_log 异步写入（fire-and-forget，失败不影响主流程）。
CREATE TABLE IF NOT EXISTS symbol_spotting_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engine_name     VARCHAR(100) NOT NULL DEFAULT 'symbol_spotting',
    backend         VARCHAR(50)  NOT NULL,       -- cadtransformer / mock / vecformer
    project_id      UUID,
    drawing_id      UUID,
    candidate_count INTEGER      DEFAULT 0,
    latency_ms      INTEGER      DEFAULT 0,
    success         BOOLEAN      NOT NULL,
    error_type      VARCHAR(200),
    created_at      TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_spotting_logs_engine_time
    ON symbol_spotting_logs (engine_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_spotting_logs_backend_time
    ON symbol_spotting_logs (backend, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_spotting_logs_drawing
    ON symbol_spotting_logs (drawing_id, created_at DESC);

-- ============================================================
-- 回滚（rollback）：按 日志表 → 引擎配置 → 模型 → 提供商 顺序删除。
-- 注意：若「本地符号识别服务」提供商被 VecFormer(C-10) 等复用，请勿删除提供商/模型。
-- ------------------------------------------------------------
-- DROP TABLE IF EXISTS symbol_spotting_logs;
--
-- DELETE FROM engine_model_configs WHERE engine_name = 'symbol_spotting';
--
-- DELETE FROM llm_models
--  WHERE model_id IN ('cadtransformer-floorplan', 'mock-spotting')
--    AND provider_id IN (SELECT id FROM llm_providers WHERE name = '本地符号识别服务');
--
-- DELETE FROM llm_providers WHERE name = '本地符号识别服务';
-- ============================================================
