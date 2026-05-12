-- ============================================================
-- Migration 001: 核心业务 Schema
-- ============================================================

-- ── 扩展 ─────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()

-- ============================================================
-- 组织架构
-- ============================================================

CREATE TABLE organizations (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         VARCHAR(200) NOT NULL,
    code         VARCHAR(50) UNIQUE,
    parent_id    UUID REFERENCES organizations(id),
    org_type     VARCHAR(20) NOT NULL DEFAULT 'company'
        CHECK (org_type IN ('group','company','branch','project_dept')),
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE users (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id           UUID REFERENCES organizations(id),
    username         VARCHAR(100) NOT NULL UNIQUE,
    email            VARCHAR(200) UNIQUE,
    hashed_password  VARCHAR(300) NOT NULL,
    display_name     VARCHAR(100) NOT NULL,
    role             VARCHAR(50) NOT NULL DEFAULT 'designer'
        CHECK (role IN (
            'group_admin',
            'group_chief_engineer',
            'group_deepening_director',
            'group_commercial_director',
            'project_manager',
            'project_chief_engineer',
            'economist',
            'designer',
            'site_engineer',
            'labor_crew'
        )),
    is_active        BOOLEAN DEFAULT true,
    last_login_at    TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_users_org ON users (org_id);
CREATE INDEX idx_users_role ON users (role);

-- ── 项目 ─────────────────────────────────────────────────────

CREATE TABLE projects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id),
    name            VARCHAR(200) NOT NULL,
    code            VARCHAR(100) UNIQUE,
    project_type    VARCHAR(50),   -- 高层住宅/大型公建/工业厂房
    annual_output   NUMERIC(18,2), -- 年产值（元），用于 KPI 红线判断
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','paused','completed')),
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE work_zones (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID NOT NULL REFERENCES projects(id),
    name        VARCHAR(200) NOT NULL,
    zone_code   VARCHAR(50),
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 图纸与审批
-- ============================================================

CREATE TABLE drawings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id),
    work_zone_id    UUID REFERENCES work_zones(id),
    drawing_no      VARCHAR(100) NOT NULL,
    title           VARCHAR(300),
    discipline      VARCHAR(50) NOT NULL
        CHECK (discipline IN ('architecture','structure','mep','decoration','general')),
    version         VARCHAR(20) NOT NULL DEFAULT 'A',
    status          VARCHAR(50) NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft',
            'ai_reviewing',
            'ai_done',
            'technical_review',
            'economic_review',
            'settlement_review',
            'published',
            'rejected'
        )),
    current_stage   VARCHAR(50),
    file_key        TEXT,                     -- MinIO 对象路径
    file_size_kb    INTEGER,
    estimated_impact NUMERIC(15,2),           -- 预估影响造价（元），≥50万升级审批
    finance_lock_status VARCHAR(20) DEFAULT 'unlocked',  -- 预留财务系统接口
    material_quota_sheet TEXT,                -- 限额领料单 MinIO key，NULL 则禁止发布
    created_by      UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_drawings_project ON drawings (project_id, status);
CREATE INDEX idx_drawings_discipline ON drawings (discipline);

CREATE TABLE drawing_versions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drawing_id  UUID NOT NULL REFERENCES drawings(id),
    version     VARCHAR(20) NOT NULL,
    file_key    TEXT NOT NULL,
    notes       TEXT,
    created_by  UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- ── 一审：技术审查 ────────────────────────────────────────────

CREATE TABLE technical_reviews (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drawing_id      UUID NOT NULL REFERENCES drawings(id),
    reviewer_id     UUID REFERENCES users(id),
    result          VARCHAR(20) CHECK (result IN ('approved','rejected','needs_revision')),
    ai_report_confirmed BOOLEAN DEFAULT false,  -- 必须确认 AI 报告才可通过
    bim_check_confirmed BOOLEAN DEFAULT false,
    issues_all_closed   BOOLEAN DEFAULT false,
    notes           TEXT,
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ── 二审：经济最优化（核心约束） ─────────────────────────────

CREATE TABLE economic_reviews (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drawing_id           UUID NOT NULL REFERENCES drawings(id),
    alternatives         JSONB NOT NULL DEFAULT '[]',  -- 多方案对比数据（≥2 方案）
    selected_option      VARCHAR(10),                  -- 选中方案编号
    total_saving_est     NUMERIC(15,2),                -- 预估节约额
    economist_id         UUID REFERENCES users(id),
    economist_signed_at  TIMESTAMPTZ,                  -- NULL = 未签字，系统锁定
    notes                TEXT,
    created_at           TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_economic_reviews_drawing ON economic_reviews (drawing_id);
CREATE INDEX idx_economic_reviews_unsigned ON economic_reviews (drawing_id)
    WHERE economist_signed_at IS NULL;

-- ── 三审：结算合规化 ──────────────────────────────────────────

CREATE TABLE settlement_reviews (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drawing_id       UUID NOT NULL REFERENCES drawings(id),
    pm_id            UUID REFERENCES users(id),       -- 项目经理
    material_mgr_id  UUID REFERENCES users(id),       -- 物资经理
    settlement_nodes JSONB DEFAULT '[]',               -- 结算节点配置
    pm_signed_at     TIMESTAMPTZ,
    material_signed_at TIMESTAMPTZ,
    notes            TEXT,
    created_at       TIMESTAMPTZ DEFAULT now()
);

-- ── AI 审查报告 ────────────────────────────────────────────────

CREATE TABLE ai_review_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drawing_id      UUID NOT NULL REFERENCES drawings(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','processing','done','failed')),
    engine_results  JSONB DEFAULT '{}',    -- 各引擎原始结果
    total_issues    INTEGER DEFAULT 0,
    critical_issues INTEGER DEFAULT 0,     -- 强条违规数量
    report_pdf_key  TEXT,                  -- 批注版 PDF，MinIO key
    report_xlsx_key TEXT,                  -- 清单版 Excel，MinIO key
    processing_ms   INTEGER,
    created_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_ai_reports_drawing ON ai_review_reports (drawing_id);

CREATE TABLE ai_review_issues (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id       UUID NOT NULL REFERENCES ai_review_reports(id) ON DELETE CASCADE,
    engine          VARCHAR(50) NOT NULL,  -- rule/kg/rag/ocr
    severity        VARCHAR(20) NOT NULL CHECK (severity IN ('critical','major','minor','info')),
    category        VARCHAR(100),
    description     TEXT NOT NULL,
    regulation_ref  TEXT,               -- 规范条文引用
    location_x      NUMERIC(10,4),      -- 图纸坐标
    location_y      NUMERIC(10,4),
    suggestion      TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','acknowledged','closed','waived')),
    closed_by       UUID REFERENCES users(id),
    closed_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_issues_report ON ai_review_issues (report_id, severity);

-- ============================================================
-- 创效激励
-- ============================================================

CREATE TABLE incentive_proposals (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       UUID NOT NULL REFERENCES projects(id),
    drawing_id       UUID REFERENCES drawings(id),
    proposer_id      UUID NOT NULL REFERENCES users(id),
    proposal_type    VARCHAR(10) NOT NULL CHECK (proposal_type IN ('A','B')),
    title            VARCHAR(300) NOT NULL,
    description      TEXT NOT NULL,
    raw_saving_est   NUMERIC(15,2),          -- 提案人粗估节约额
    status           VARCHAR(50) NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft','calculating','pending_sign',
            'public_notice','distributing','approved','paid','rejected'
        )),
    net_saving       NUMERIC(15,2),          -- 商务核算净节约额
    cost_snapshot    JSONB,                  -- 测算过程快照（防争议）
    notice_ends_at   TIMESTAMPTZ,            -- 公示期结束时间
    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_proposals_project ON incentive_proposals (project_id, status);

CREATE TABLE proposal_approvals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id     UUID NOT NULL REFERENCES incentive_proposals(id),
    role            VARCHAR(50) NOT NULL,    -- project_manager/economist/group_director
    approver_id     UUID REFERENCES users(id),
    signed_at       TIMESTAMPTZ,
    comment         TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE bonus_distributions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id     UUID NOT NULL REFERENCES incentive_proposals(id),
    -- 铁三角分配（硬编码比例，前端不得修改）
    group_amount    NUMERIC(15,2),           -- 20%
    team_pool       NUMERIC(15,2),           -- 50%
    proposer_amount NUMERIC(15,2),           -- 30%
    team_breakdown  JSONB DEFAULT '[]',      -- 项目部内部二次分配
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE bonus_payments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    distribution_id UUID NOT NULL REFERENCES bonus_distributions(id),
    recipient_id    UUID NOT NULL REFERENCES users(id),
    amount          NUMERIC(15,2) NOT NULL,
    payment_type    VARCHAR(20) NOT NULL,    -- salary/separate
    paid_at         TIMESTAMPTZ,
    voucher_pdf_key TEXT,                    -- 兑现凭证 MinIO key
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 材料单价库
-- ============================================================

CREATE TABLE material_prices (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    material_no  VARCHAR(100) NOT NULL,
    name         VARCHAR(200) NOT NULL,
    spec         VARCHAR(100),
    unit         VARCHAR(20) NOT NULL,
    unit_price   NUMERIC(12,4) NOT NULL,
    price_date   DATE NOT NULL,
    source       VARCHAR(100),
    updated_by   UUID REFERENCES users(id),
    updated_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (material_no, price_date)
);

-- ============================================================
-- 规范知识库
-- ============================================================

CREATE TABLE regulation_books (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title        VARCHAR(300) NOT NULL,
    std_no       VARCHAR(100) UNIQUE,         -- GB50010-2010
    version      VARCHAR(50),
    discipline   VARCHAR(50),                 -- structure/fire/mep/general
    publisher    VARCHAR(200),
    effective_at DATE,
    status       VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('draft','active','superseded','withdrawn')),
    source_type  VARCHAR(20) NOT NULL DEFAULT 'manual'
        CHECK (source_type IN ('manual','file_import','api_sync')),
    file_key     TEXT,                        -- 原始文件 MinIO key
    created_by   UUID REFERENCES users(id),
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE regulation_articles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    book_id         UUID NOT NULL REFERENCES regulation_books(id) ON DELETE CASCADE,
    article_no      VARCHAR(50) NOT NULL,     -- "4.2.3"
    title           VARCHAR(300),
    content         TEXT NOT NULL,
    obligation_level VARCHAR(20) NOT NULL DEFAULT 'SHOULD'
        CHECK (obligation_level IN ('MUST','SHOULD','MAY','MUST_NOT')),
    is_mandatory    BOOLEAN DEFAULT false,     -- 强制性条文
    conditions      JSONB DEFAULT '[]',        -- 适用条件（KG 推理用）
    age_node_id     BIGINT,                    -- Apache AGE 图节点 ID
    vector_id       VARCHAR(200),              -- Chroma 向量 ID
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (book_id, article_no)
);

CREATE INDEX idx_articles_book ON regulation_articles (book_id, is_mandatory);
CREATE INDEX idx_articles_mandatory ON regulation_articles (is_mandatory, obligation_level);

-- 外部 API 同步配置
CREATE TABLE regulation_api_sources (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR(200) NOT NULL,
    endpoint_url  TEXT NOT NULL,
    auth_type     VARCHAR(20) DEFAULT 'api_key',  -- api_key/oauth/none
    auth_config   JSONB DEFAULT '{}',             -- 不存明文，存配置结构
    sync_interval_hours INTEGER DEFAULT 24,
    last_synced_at TIMESTAMPTZ,
    is_active     BOOLEAN DEFAULT true,
    created_by    UUID REFERENCES users(id),
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- 标准图集
CREATE TABLE standard_drawings (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code        VARCHAR(100) UNIQUE,
    title       VARCHAR(300) NOT NULL,
    category    VARCHAR(100),
    version     VARCHAR(50),
    status      VARCHAR(20) DEFAULT 'draft'
        CHECK (status IN ('draft','reviewing','published','archived')),
    file_key    TEXT,
    approved_by UUID REFERENCES users(id),
    approved_at TIMESTAMPTZ,
    created_by  UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 错漏碰缺案例库
CREATE TABLE defect_cases (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   UUID REFERENCES projects(id),
    drawing_id   UUID REFERENCES drawings(id),
    category     VARCHAR(50),                 -- 错/漏/碰/缺
    discipline   VARCHAR(50),
    title        VARCHAR(300) NOT NULL,
    description  TEXT NOT NULL,
    root_cause   TEXT,
    solution     TEXT,
    original_img_key TEXT,
    corrected_img_key TEXT,
    vector_id    VARCHAR(200),               -- Chroma 向量 ID（相似案例检索）
    created_by   UUID REFERENCES users(id),
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 审计日志（只追加，不可修改）
-- ============================================================

CREATE TABLE audit_logs (
    id           BIGSERIAL PRIMARY KEY,
    user_id      UUID REFERENCES users(id),
    action       VARCHAR(100) NOT NULL,       -- create_drawing/sign_economic_review/...
    resource     VARCHAR(50),                 -- drawing/proposal/user/...
    resource_id  UUID,
    old_state    JSONB,
    new_state    JSONB,
    ip_address   INET,
    user_agent   TEXT,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_audit_resource ON audit_logs (resource, resource_id, created_at DESC);
CREATE INDEX idx_audit_user ON audit_logs (user_id, created_at DESC);

-- ── 初始数据：集团组织 + 管理员账户 ─────────────────────────

INSERT INTO organizations (name, code, org_type)
VALUES ('集团总部', 'HQ', 'group');

-- 密码使用 bcrypt hash，明文为 "Admin@2026!" 仅供初始化
INSERT INTO users (org_id, username, email, hashed_password, display_name, role)
SELECT id, 'admin', 'admin@cad.local',
       '$2b$12$placeholder_hash_replace_on_first_login',
       '系统管理员', 'group_admin'
FROM organizations WHERE code = 'HQ';
