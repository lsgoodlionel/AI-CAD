-- ============================================================
-- Migration 006: 项目管理 / 人员管理增强
-- ============================================================

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS start_date DATE,
    ADD COLUMN IF NOT EXISTS end_date DATE,
    ADD COLUMN IF NOT EXISTS manager_id UUID REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS chief_engineer_id UUID REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS commercial_manager_id UUID REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS phone VARCHAR(50),
    ADD COLUMN IF NOT EXISTS position VARCHAR(100),
    ADD COLUMN IF NOT EXISTS employee_no VARCHAR(100) UNIQUE,
    ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

CREATE TABLE IF NOT EXISTS project_members (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_role VARCHAR(50) NOT NULL
        CHECK (project_role IN (
            'project_manager',
            'project_chief_engineer',
            'commercial_manager',
            'economist',
            'designer',
            'site_engineer',
            'labor_crew',
            'viewer'
        )),
    is_primary   BOOLEAN DEFAULT false,
    joined_at    DATE DEFAULT CURRENT_DATE,
    left_at      DATE,
    created_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (project_id, user_id, project_role)
);

CREATE INDEX IF NOT EXISTS idx_project_members_project ON project_members (project_id);
CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members (user_id);

-- 本地/E2E 既有数据补齐成员关系，避免升级后项目对非管理员不可见。
INSERT INTO project_members (project_id, user_id, project_role, is_primary)
SELECT p.id, u.id,
       CASE
           WHEN u.role = 'project_manager' THEN 'project_manager'
           WHEN u.role = 'project_chief_engineer' THEN 'project_chief_engineer'
           WHEN u.role = 'group_commercial_director' THEN 'commercial_manager'
           WHEN u.role = 'economist' THEN 'economist'
           WHEN u.role = 'site_engineer' THEN 'site_engineer'
           WHEN u.role = 'labor_crew' THEN 'labor_crew'
           ELSE 'designer'
       END,
       u.role IN ('project_manager', 'project_chief_engineer', 'economist')
FROM projects p
JOIN users u ON u.is_active = true
WHERE u.role <> 'group_admin'
ON CONFLICT (project_id, user_id, project_role) DO NOTHING;
