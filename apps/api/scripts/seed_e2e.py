"""Seed deterministic data for local E2E tests."""
from __future__ import annotations

import asyncio
import os

import asyncpg

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://cad_user:cad_pass@127.0.0.1:5432/cad_db",
)

ORG_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
ADMIN_ID = "33333333-3333-3333-3333-333333333333"
PM_ID = "44444444-4444-4444-4444-444444444444"
ECONOMIST_ID = "55555555-5555-5555-5555-555555555555"
DESIGNER_ID = "66666666-6666-6666-6666-666666666666"
DRAWING_ID = "77777777-7777-7777-7777-777777777777"
REPORT_ID = "88888888-8888-8888-8888-888888888888"
PROPOSAL_ID = "99999999-9999-9999-9999-999999999999"

ADMIN_HASH = "$2b$12$XEZ2jE5fSqhi.RtuViEoPeGNXl9b2TikBXM8ce6BiWi5XOMpDHrdC"
PM_HASH = "$2b$12$O.QROF/fczYVAV7LGEmy5uv9hV7xfyF6AKkJlBc50G2LF.F4/9TFK"
ECONOMIST_HASH = "$2b$12$5N3ohSkYEVX9YtzQdZdECegZ6R7Wgr.eTK9NKuwlFbHwzUO8A5pKm"
DESIGNER_HASH = "$2b$12$eZTll5Jmr0xBwNxcWQO8tudDV0KAodZK/JvwfG47/BikuORjp69Gi"


async def seed() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO organizations (id, name, code, org_type)
                VALUES ($1, 'E2E集团', 'E2E-HQ', 'group')
                ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, code=EXCLUDED.code
                """,
                ORG_ID,
            )
            await conn.execute(
                """
                INSERT INTO users (id, org_id, username, email, hashed_password, display_name, role)
                VALUES
                    ($1,$5,'admin','admin@cad.local',$6,'系统管理员','group_admin'),
                    ($2,$5,'pm','pm@cad.local',$7,'项目经理','project_manager'),
                    ($3,$5,'economist','economist@cad.local',$8,'经济师','economist'),
                    ($4,$5,'designer','designer@cad.local',$9,'深化设计师','designer')
                ON CONFLICT (username) DO UPDATE SET
                    org_id=EXCLUDED.org_id,
                    email=EXCLUDED.email,
                    hashed_password=EXCLUDED.hashed_password,
                    display_name=EXCLUDED.display_name,
                    role=EXCLUDED.role,
                    is_active=true
                """,
                ADMIN_ID,
                PM_ID,
                ECONOMIST_ID,
                DESIGNER_ID,
                ORG_ID,
                ADMIN_HASH,
                PM_HASH,
                ECONOMIST_HASH,
                DESIGNER_HASH,
            )
            admin_id = await conn.fetchval("SELECT id FROM users WHERE username='admin'")
            pm_id = await conn.fetchval("SELECT id FROM users WHERE username='pm'")
            economist_id = await conn.fetchval("SELECT id FROM users WHERE username='economist'")
            designer_id = await conn.fetchval("SELECT id FROM users WHERE username='designer'")
            await conn.execute(
                """
                INSERT INTO projects (id, org_id, name, code, project_type, annual_output, status, created_by)
                VALUES ($1, $2, 'E2E示范项目', 'E2E-PROJECT', '高层住宅', 120000000, 'active', $3)
                ON CONFLICT (id) DO UPDATE SET
                    name=EXCLUDED.name,
                    annual_output=EXCLUDED.annual_output,
                    status=EXCLUDED.status
                """,
                PROJECT_ID,
                ORG_ID,
                admin_id,
            )
            await conn.execute(
                """
                INSERT INTO drawings (
                    id, project_id, drawing_no, title, discipline, version, status,
                    current_stage, file_key, file_size_kb, estimated_impact, created_by, updated_at
                )
                VALUES (
                    $1, $2, 'E2E-ARCH-001', 'E2E 建筑深化样图', 'architecture', 'A',
                    'ai_done', 'AI审图完成', 'projects/e2e/drawings/e2e-arch-001.pdf',
                    128, 650000, $3, now()
                )
                ON CONFLICT (id) DO UPDATE SET
                    status=EXCLUDED.status,
                    current_stage=EXCLUDED.current_stage,
                    updated_at=now()
                """,
                DRAWING_ID,
                PROJECT_ID,
                designer_id,
            )
            await conn.execute(
                """
                INSERT INTO ai_review_reports (
                    id, drawing_id, status, engine_results, total_issues,
                    critical_issues, completed_at
                )
                VALUES ($1, $2, 'done', '{"rule":"done","kg":"done"}'::jsonb, 2, 1, now())
                ON CONFLICT (id) DO UPDATE SET
                    status=EXCLUDED.status,
                    total_issues=EXCLUDED.total_issues,
                    critical_issues=EXCLUDED.critical_issues,
                    completed_at=now()
                """,
                REPORT_ID,
                DRAWING_ID,
            )
            await conn.execute("DELETE FROM ai_review_issues WHERE report_id=$1", REPORT_ID)
            await conn.execute(
                """
                INSERT INTO ai_review_issues (
                    report_id, engine, severity, category, description,
                    regulation_ref, location_x, location_y, suggestion, status
                )
                VALUES
                    ($1, 'rule', 'critical', '强制性条文', '疏散宽度不满足规范要求', 'GB50016-2014 5.5.18', 0.35, 0.42, '复核疏散宽度并调整墙线', 'open'),
                    ($1, 'kg', 'minor', '图纸表达', '门窗编号缺少索引说明', 'GB/T 50001-2017', null, null, '补充门窗编号索引', 'acknowledged')
                """,
                REPORT_ID,
            )
            await conn.execute(
                """
                INSERT INTO incentive_proposals (
                    id, project_id, drawing_id, proposer_id, proposal_type, title,
                    description, raw_saving_est, status, net_saving, cost_snapshot, updated_at
                )
                VALUES (
                    $1, $2, $3, $4, 'A', 'E2E 钢筋翻样优化',
                    '通过优化钢筋下料组合减少损耗。', 180000, 'pending_sign', 150000,
                    '{"bonus_pool":22500,"group_amount":4500,"team_pool":11250,"proposer_amount":6750}'::jsonb,
                    now()
                )
                ON CONFLICT (id) DO UPDATE SET
                    status=EXCLUDED.status,
                    net_saving=EXCLUDED.net_saving,
                    updated_at=now()
                """,
                PROPOSAL_ID,
                PROJECT_ID,
                DRAWING_ID,
                designer_id,
            )
            await conn.execute("DELETE FROM proposal_approvals WHERE proposal_id=$1", PROPOSAL_ID)
            await conn.execute(
                """
                INSERT INTO proposal_approvals (proposal_id, role, approver_id, signed_at, comment)
                VALUES
                    ($1, 'project_manager', $2, now(), 'E2E 项目经理确认'),
                    ($1, 'economist', $3, null, null)
                """,
                PROPOSAL_ID,
                pm_id,
                economist_id,
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())
