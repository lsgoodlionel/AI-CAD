-- ============================================================
-- Migration 026: Finding 统一模型（Phase D · 泳道2 · D-05）
-- ============================================================
-- 蓝图来源：docs/PHASE_D_BLUEPRINT.md § D-架构-3 / 泳道2 D-05。
-- 目标：把五类割裂的问题/发现（单图 AI 审图 ai_review_issues、会审
-- review_audit_findings、跨图 review_batches.cross_findings、语义审校
-- （project_models.scene 派生，无持久行）、符号待审 model_symbol_annotations）
-- 统一到一个 Finding 读取聚合层，配一套独立的人工闭环状态机。
--
-- 设计取舍（重要）：
-- 本迁移**不**修改上述任何来源表的结构或写入路径（硬约束）。真正的跨源聚合
-- 在 Python 层完成（services/finding_service.py），因为语义审校项和跨图问题
-- 并非持久化的表行，而是从 JSONB（scene / cross_findings）在请求时确定性派生
-- 的——SQL 视图无法优雅覆盖这种动态派生，纯读取聚合放在应用层更简单可测。
--
-- 本迁移只新增一张**状态覆盖表** finding_status：作为独立的人工闭环状态机
-- （待处理 pending → 已确认 acknowledged → 已整改 remediated → 已闭环 closed），
-- 不回写到 ai_review_issues.status / model_symbol_annotations.status 等来源表
-- 自身的状态列——五个来源各自的原生状态语义不一致（如 open/closed/waived 对比
-- pending/confirmed/rejected/reclassed），强行合并会引入歧义；改为一张统一
-- 状态覆盖表，以 (project_id, source, source_key) 为自然键幂等 upsert。
--
-- 可重复执行（IF NOT EXISTS）；向后兼容（新表，不改动既有表）。
-- ============================================================

CREATE TABLE IF NOT EXISTS finding_status (
    id           BIGSERIAL PRIMARY KEY,
    project_id   UUID NOT NULL REFERENCES projects(id),
    -- 五类来源：engine=单图AI审图 / review=会审发现 / cross=跨图套图问题 /
    -- semantic=语义审校项 / symbol=符号待审项
    source       VARCHAR(16) NOT NULL
        CHECK (source IN ('engine', 'review', 'cross', 'semantic', 'symbol')),
    -- 来源表内的自然标识（字符串化）：engine/review 用来源表 UUID 主键；
    -- symbol 用 model_symbol_annotations.id（bigint）字符串化；semantic 用
    -- build_review_queue 派生的稳定 id（如 "host:o1"）；cross 用
    -- "{batch_id}:{category}:{key}" 组合 key（同批次内幂等）。
    source_key   TEXT NOT NULL,
    status       VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'acknowledged', 'remediated', 'closed')),
    note         TEXT,
    updated_by   UUID REFERENCES users(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, source, source_key)
);

CREATE INDEX IF NOT EXISTS idx_finding_status_project
    ON finding_status (project_id);
CREATE INDEX IF NOT EXISTS idx_finding_status_lookup
    ON finding_status (project_id, source, source_key);
CREATE INDEX IF NOT EXISTS idx_finding_status_status
    ON finding_status (project_id, status);

COMMENT ON TABLE finding_status IS
    'Phase D D-05 Finding 统一读取聚合层的独立闭环状态机覆盖表；不回写五个来源表';

-- ============================================================
-- 回滚（手动执行）
-- ------------------------------------------------------------
-- DROP TABLE IF EXISTS finding_status;
-- ============================================================
