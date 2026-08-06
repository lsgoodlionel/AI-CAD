-- 040: 人工标注学习闭环(标注 → 学习 → 建议 → 人审采纳 → 生效)
--
-- 目标:每次人工标注都反哺系统,下次自动识别更准更全,人工量逐步下降。
-- 四张表各司其职:
--   annotation_events        原始事件:人改了什么、系统原本猜的是什么(学习的燃料)
--   optimization_runs        每次分析的过程日志(可实时查看)
--   improvement_suggestions  分析产出的建议 + 人审状态
--   learned_rules            **采纳后真正生效**的规则(被识别路径读取)
--
-- 关键区分:建议分「可自动生效」与「需开发介入」两类——
-- 前者采纳即写 learned_rules 当场生效,后者导出给开发,绝不假装已解决。

CREATE TABLE IF NOT EXISTS annotation_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   UUID REFERENCES projects(id) ON DELETE CASCADE,
    drawing_id   UUID REFERENCES drawings(id) ON DELETE SET NULL,
    kind         TEXT NOT NULL,        -- discipline|axis|scale|title_block|component|…
    field        TEXT,                 -- 具体字段
    auto_value   TEXT,                 -- 系统当时的自动值(可空=系统没给出)
    human_value  TEXT,                 -- 人给的值
    context_json JSONB,                -- 现场信息(区域/原文/候选数…)
    created_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE annotation_events IS
    '人工标注事件:auto_value 与 human_value 的差异就是学习信号';
CREATE INDEX IF NOT EXISTS idx_ann_ev_project ON annotation_events(project_id, kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ann_ev_unprocessed ON annotation_events(created_at);

CREATE TABLE IF NOT EXISTS optimization_runs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     UUID REFERENCES projects(id) ON DELETE CASCADE,
    trigger        TEXT NOT NULL,      -- manual|auto|scheduled
    events_scanned INT NOT NULL DEFAULT 0,
    findings       INT NOT NULL DEFAULT 0,
    steps_json     JSONB,              -- 逐步过程日志(实时查看用)
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    error          TEXT
);
COMMENT ON TABLE optimization_runs IS '每次学习分析的过程日志,可实时查看';

CREATE TABLE IF NOT EXISTS improvement_suggestions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id         UUID REFERENCES optimization_runs(id) ON DELETE CASCADE,
    project_id     UUID REFERENCES projects(id) ON DELETE CASCADE,
    category       TEXT NOT NULL,      -- vocabulary|threshold|ocr_correction|template|algorithm
    title          TEXT NOT NULL,
    detail         TEXT NOT NULL,      -- 人能读懂的说明:凭什么这么建议
    evidence_json  JSONB,              -- 支撑证据(样本/计数),供人核对
    impact         INT NOT NULL DEFAULT 0,   -- 预估影响图纸数
    confidence     REAL NOT NULL DEFAULT 0,
    auto_applicable BOOLEAN NOT NULL DEFAULT false,  -- 采纳后能否当场生效
    status         TEXT NOT NULL DEFAULT 'pending',  -- pending|accepted|rejected|exported
    applied_at     TIMESTAMPTZ,
    reviewed_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sugg_project ON improvement_suggestions(project_id, status, impact DESC);

CREATE TABLE IF NOT EXISTS learned_rules (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    UUID REFERENCES projects(id) ON DELETE CASCADE,  -- NULL=全局
    rule_type     TEXT NOT NULL,       -- vocabulary|threshold|ocr_correction
    rule_key      TEXT NOT NULL,
    rule_value    TEXT NOT NULL,
    source_suggestion_id UUID REFERENCES improvement_suggestions(id) ON DELETE SET NULL,
    hit_count     INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, rule_type, rule_key)
);
COMMENT ON TABLE learned_rules IS
    '采纳后真正生效的学习规则,被识别路径读取(词表/阈值/OCR 纠错)';
CREATE INDEX IF NOT EXISTS idx_learned_type ON learned_rules(rule_type, project_id);
