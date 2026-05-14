-- ============================================================
-- Migration 002: 模型路由管理 + 引擎参数配置
-- ============================================================

-- ── 提供商 ───────────────────────────────────────────────────
CREATE TABLE llm_providers (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR(100) NOT NULL UNIQUE,
    provider_type VARCHAR(50)  NOT NULL
        CHECK (provider_type IN ('anthropic','openai_compat','ollama','custom_http')),
    base_url      TEXT,
    api_key_env   VARCHAR(100),          -- 环境变量名，不存明文
    timeout_sec   INTEGER DEFAULT 120,
    is_active     BOOLEAN DEFAULT true,
    metadata      JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- ── 模型 ─────────────────────────────────────────────────────
CREATE TABLE llm_models (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id         UUID NOT NULL REFERENCES llm_providers(id) ON DELETE CASCADE,
    model_id            VARCHAR(200) NOT NULL,      -- "claude-sonnet-4-6"
    display_name        VARCHAR(200) NOT NULL,
    context_window      INTEGER,
    supports_vision     BOOLEAN DEFAULT false,
    input_price_per_1m  NUMERIC(10,4) DEFAULT 0,   -- USD/百万 token，本地填 0
    output_price_per_1m NUMERIC(10,4) DEFAULT 0,
    benchmark_score     NUMERIC(5,2),
    is_active           BOOLEAN DEFAULT true,
    created_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (provider_id, model_id)
);

-- ── 引擎模型配置 ──────────────────────────────────────────────
CREATE TABLE engine_model_configs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engine_name             VARCHAR(100) NOT NULL,
    task_type               VARCHAR(50)  NOT NULL
        CHECK (task_type IN ('primary','fallback_1','fallback_2','batch')),
    model_id                UUID NOT NULL REFERENCES llm_models(id),
    temperature             NUMERIC(4,2)  DEFAULT 0.10,
    max_tokens              INTEGER       DEFAULT 2048,
    top_p                   NUMERIC(4,2)  DEFAULT 1.00,
    frequency_penalty       NUMERIC(4,2)  DEFAULT 0.00,
    prompt_template_version VARCHAR(50),
    extra_params            JSONB,         -- custom_http 请求模板等
    is_enabled              BOOLEAN DEFAULT true,
    updated_at              TIMESTAMPTZ DEFAULT now(),
    updated_by              UUID REFERENCES users(id),
    UNIQUE (engine_name, task_type)
);

CREATE INDEX idx_emc_engine ON engine_model_configs (engine_name, task_type)
    WHERE is_enabled = true;

-- ── 引擎业务参数（知识图谱 + 经济测算） ──────────────────────
CREATE TABLE engine_params (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope       VARCHAR(50)  NOT NULL,   -- 'kg' | 'economic' | 'ai_review' | 'rebar'
    param_key   VARCHAR(100) NOT NULL,
    param_value JSONB        NOT NULL,
    description TEXT,
    updated_at  TIMESTAMPTZ DEFAULT now(),
    updated_by  UUID REFERENCES users(id),
    UNIQUE (scope, param_key)
);

-- ── 调用日志 ──────────────────────────────────────────────────
CREATE TABLE llm_call_logs (
    id                UUID DEFAULT gen_random_uuid(),
    engine_name       VARCHAR(100),
    model_db_id       UUID REFERENCES llm_models(id),
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    latency_ms        INTEGER DEFAULT 0,
    cost_usd          NUMERIC(12,8) DEFAULT 0,
    success           BOOLEAN NOT NULL,
    error_type        VARCHAR(200),
    created_at        TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- 按月分区（生产环境建议改为按周）
CREATE TABLE llm_call_logs_2026_05 PARTITION OF llm_call_logs
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE llm_call_logs_2026_06 PARTITION OF llm_call_logs
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE INDEX idx_logs_engine_time ON llm_call_logs (engine_name, created_at DESC);
CREATE INDEX idx_logs_model_time  ON llm_call_logs (model_db_id, created_at DESC);

-- ── 提示词模板版本管理 ────────────────────────────────────────
CREATE TABLE prompt_templates (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engine_name VARCHAR(100) NOT NULL,
    task_type   VARCHAR(100) NOT NULL,
    version     VARCHAR(50)  NOT NULL,
    template    TEXT         NOT NULL,
    is_active   BOOLEAN DEFAULT false,  -- 同一 engine+task_type 只有一个 active
    created_at  TIMESTAMPTZ DEFAULT now(),
    created_by  UUID REFERENCES users(id),
    UNIQUE (engine_name, task_type, version)
);

CREATE UNIQUE INDEX idx_pt_active ON prompt_templates (engine_name, task_type)
    WHERE is_active = true;

-- ── 初始数据：内置提供商 ──────────────────────────────────────
INSERT INTO llm_providers (name, provider_type, base_url, api_key_env) VALUES
    ('Claude API',  'anthropic',     null,                         'ANTHROPIC_API_KEY'),
    ('OpenAI',      'openai_compat', 'https://api.openai.com/v1',  'OPENAI_API_KEY'),
    ('DeepSeek',    'openai_compat', 'https://api.deepseek.com/v1','DEEPSEEK_API_KEY'),
    ('Ollama 本地', 'ollama',        'http://host.docker.internal:11434', null);

-- ── 初始数据：内置模型 ────────────────────────────────────────
INSERT INTO llm_models (provider_id, model_id, display_name, context_window, supports_vision, input_price_per_1m, output_price_per_1m)
SELECT p.id, m.model_id, m.display_name, m.ctx, m.vision, m.inp, m.out
FROM llm_providers p
CROSS JOIN LATERAL (VALUES
    ('Claude API','claude-sonnet-4-6',       'Claude Sonnet 4.6',   200000, true,  3.0, 15.0),
    ('Claude API','claude-haiku-4-5-20251001','Claude Haiku 4.5',   200000, false, 0.25, 1.25),
    ('OpenAI',    'gpt-4o',                  'GPT-4o',              128000, true,  2.5,  10.0),
    ('OpenAI',    'gpt-4o-mini',             'GPT-4o Mini',         128000, false, 0.15,  0.6),
    ('DeepSeek',  'deepseek-chat',           'DeepSeek Chat',        64000, false, 0.14,  0.28),
    ('DeepSeek',  'deepseek-reasoner',       'DeepSeek Reasoner',    64000, false, 0.55,  2.19),
    ('Ollama 本地','qwen2.5:72b',            'Qwen2.5 72B (本地)',  128000, false, 0.0,   0.0),
    ('Ollama 本地','deepseek-r1:14b',        'DeepSeek-R1 14B (本地)',32000, false, 0.0,  0.0)
) AS m(pname, model_id, display_name, ctx, vision, inp, out)
WHERE p.name = m.pname;
