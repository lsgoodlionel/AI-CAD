-- ════════════════════════════════════════════════════════════════════════
-- 024_review_actions.sql — Phase C 泳道 D 前端审校共享数据契约
--
-- 目标：为 C-15（语义审校）/ C-16（符号标注）/ C-17（返工度量看板）提供共享的
--   ① 人审动作埋点表 model_review_actions —— C-15/C-16 写，C-17 聚合读；
--   ② 符号级标注表 model_symbol_annotations —— C-16 写（候选框+置信度+金标签状态），
--      C-06 金标签生产 / C-09 训练导出复用。
--
-- 前置：023_symbol_spotting.sql（泳道 C）。编号延续 019–023。
-- 约定：无硬 FK（对齐既有 model_* 分析/标注表松耦合风格）；jsonb 存 bbox；
--       幂等（IF NOT EXISTS）；含回滚注释。
-- ════════════════════════════════════════════════════════════════════════

-- ── §1 人审动作埋点（返工点收敛度量的单一数据源）──────────────────────────
CREATE TABLE IF NOT EXISTS model_review_actions (
    id            BIGSERIAL PRIMARY KEY,
    project_id    TEXT NOT NULL,
    drawing_id    TEXT,
    -- 审校对象类别：symbol（符号）/ element（构件）/ topology（拓扑）/
    --              naming（命名）/ compliance（规范符合性）
    target_kind   TEXT NOT NULL,
    target_id     TEXT,
    -- 动作：confirm 确认 / reject 否定 / reclass 改类 / addbox 补框 / edit 编辑
    action_type   TEXT NOT NULL,
    old_category  TEXT,
    new_category  TEXT,
    mep_system    TEXT,
    discipline    TEXT,               -- 专业：结构/机电/装修/建筑
    source        TEXT,               -- 被审对象来源：rule / model / fused
    confidence    DOUBLE PRECISION,   -- 被审对象置信度（审前）
    reviewer_id   TEXT,
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_review_actions_project
    ON model_review_actions (project_id);
CREATE INDEX IF NOT EXISTS idx_review_actions_project_time
    ON model_review_actions (project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_review_actions_dim
    ON model_review_actions (discipline, target_kind, action_type);

COMMENT ON TABLE model_review_actions IS
    'Phase C 泳道D 人审动作埋点：确认/否定/改类/补框；C-17 据此算返工率收敛趋势';

-- ── §2 符号级标注（生产审校 + C-06 金标签 + C-09 训练导出）─────────────────
CREATE TABLE IF NOT EXISTS model_symbol_annotations (
    id            BIGSERIAL PRIMARY KEY,
    project_id    TEXT NOT NULL,
    drawing_id    TEXT NOT NULL,
    category      TEXT NOT NULL,          -- 9 类 taxonomy 或其子类
    mep_system    TEXT,                   -- 消防/给排水/电气/暖通 或 NULL
    bbox          JSONB NOT NULL,         -- [x_min, y_min, x_max, y_max]
    confidence    DOUBLE PRECISION,       -- 模型候选置信度
    -- 来源：model 学习模型候选 / human 人工新增框
    source        TEXT NOT NULL DEFAULT 'model',
    -- 状态：pending 待审 / confirmed 已确认 / rejected 已否定 / reclassed 已改类
    status        TEXT NOT NULL DEFAULT 'pending',
    primitive_ids JSONB,                  -- 关联 PrimitiveDoc 图元 id 列表
    reviewer_id   TEXT,
    evidence      JSONB,                  -- {backend, label_source, ...}
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_symbol_annot_project_drawing
    ON model_symbol_annotations (project_id, drawing_id);
CREATE INDEX IF NOT EXISTS idx_symbol_annot_status
    ON model_symbol_annotations (project_id, status);

COMMENT ON TABLE model_symbol_annotations IS
    'Phase C 泳道D 符号级标注：候选框+置信度+金标签状态；C-06 精标注 / C-09 训练导出复用';

-- ── 回滚（手动执行）────────────────────────────────────────────────────────
-- DROP TABLE IF EXISTS model_symbol_annotations;
-- DROP TABLE IF EXISTS model_review_actions;
