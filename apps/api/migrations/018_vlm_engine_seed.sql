-- ============================================================
-- Migration 018: VLM 语义引擎种子数据（Phase A · A-10）
-- ============================================================
-- 目标：为第 14 个引擎 `drawing_semantic_vlm` 种子模型路由配置，
--       用于「读图名 / 标题栏 / 判专业 / 跨图提示」，本地/云端可热切换。
--
-- 依赖：002_model_management.sql（llm_providers / llm_models / engine_model_configs）。
--
-- 幂等：全部 INSERT 使用 ON CONFLICT DO NOTHING，可重复执行。
-- 执行：psql "$DATABASE_URL" -f apps/api/migrations/018_vlm_engine_seed.sql
--
-- 说明：本引擎依赖具体的“视觉模型”行。若你的模型管理里已有自定义的
--       Qwen-VL 模型，可跳过 §1/§2，直接在管理后台「引擎配置」为
--       `drawing_semantic_vlm` 选择对应模型；此处仅提供开箱即用的默认种子：
--         · primary    = 云端 DashScope qwen-vl-max（无 GPU 也可跑）
--         · fallback_1 = 本地 Ollama qwen2.5vl:7b（涉密图纸建议切为 primary）
--       provider 类型沿用 002 内置的 openai_compat / ollama，无需新增类型。
-- ============================================================

-- ── §1 提供商：阿里云 DashScope（OpenAI 兼容模式，云端 Qwen-VL）─────────────
-- name 唯一，重复执行不产生副本。api_key 只存环境变量名（DASHSCOPE_API_KEY）。
INSERT INTO llm_providers (name, provider_type, base_url, api_key_env)
VALUES (
    '阿里云 DashScope',
    'openai_compat',
    'https://dashscope.aliyuncs.com/compatible-mode/v1',
    'DASHSCOPE_API_KEY'
)
ON CONFLICT (name) DO NOTHING;

-- ── §2 模型：Qwen-VL 视觉模型（云端 + 本地）────────────────────────────────
-- UNIQUE(provider_id, model_id) 保证幂等。价格为参考值（USD/百万 token），本地填 0。
INSERT INTO llm_models
    (provider_id, model_id, display_name, context_window, supports_vision, input_price_per_1m, output_price_per_1m)
SELECT p.id, m.model_id, m.display_name, m.ctx, m.vision, m.inp, m.out
FROM llm_providers p
CROSS JOIN LATERAL (VALUES
    ('阿里云 DashScope', 'qwen-vl-max',   'Qwen-VL-Max (云端)',      32000, true, 0.80, 3.20),
    ('Ollama 本地',      'qwen2.5vl:7b',  'Qwen2.5-VL 7B (本地)',    32000, true, 0.00, 0.00)
) AS m(pname, model_id, display_name, ctx, vision, inp, out)
WHERE p.name = m.pname
ON CONFLICT (provider_id, model_id) DO NOTHING;

-- ── §3 引擎配置：drawing_semantic_vlm ─────────────────────────────────────
-- UNIQUE(engine_name, task_type) 保证幂等。temperature 低（语义抽取需稳定），
-- max_tokens 适中（标题栏字段 + 跨图提示 JSON 输出）。
INSERT INTO engine_model_configs
    (engine_name, task_type, model_id, temperature, max_tokens, top_p)
SELECT 'drawing_semantic_vlm', cfg.task_type, lm.id, cfg.temperature, cfg.max_tokens, cfg.top_p
FROM (VALUES
    -- primary：云端 DashScope（无 GPU 也可用；涉密图纸请在后台切为本地）
    ('primary',    '阿里云 DashScope', 'qwen-vl-max',  0.10, 2048, 0.90),
    -- fallback_1：本地 Ollama（隐私优先场景可提升为 primary）
    ('fallback_1', 'Ollama 本地',      'qwen2.5vl:7b', 0.10, 2048, 0.90)
) AS cfg(task_type, pname, model_id, temperature, max_tokens, top_p)
JOIN llm_providers lp ON lp.name = cfg.pname
JOIN llm_models    lm ON lm.provider_id = lp.id AND lm.model_id = cfg.model_id
ON CONFLICT (engine_name, task_type) DO NOTHING;

-- ============================================================
-- 回滚（rollback）：按引擎配置 → 模型 → 提供商 顺序删除。
-- 注意：DashScope 提供商 / 本地 Qwen 模型若被其它引擎复用，请勿删除。
-- ------------------------------------------------------------
-- DELETE FROM engine_model_configs WHERE engine_name = 'drawing_semantic_vlm';
--
-- DELETE FROM llm_models
--  WHERE model_id IN ('qwen-vl-max', 'qwen2.5vl:7b')
--    AND provider_id IN (
--        SELECT id FROM llm_providers WHERE name IN ('阿里云 DashScope', 'Ollama 本地')
--    );
--
-- DELETE FROM llm_providers WHERE name = '阿里云 DashScope';
-- ============================================================
