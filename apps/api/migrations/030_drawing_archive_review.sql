-- ════════════════════════════════════════════════════════════════════════
-- 030_drawing_archive_review.sql — Phase E1.5 图纸信息档案:人审层 + 状态机
--
-- 目标：把 029 的 drawing_extracted_info 升级为「单一真相源」——
--   ① 人审 verified 层：auto 抽取值与人工修正值分离，verified 永远优先且
--      不被重抽覆盖；被推翻的 auto 行保留（is_active=false）供审图追溯
--      「AI 原读成啥 / 人改成啥」。
--   ② 每图档案状态机 drawing_archive_status：pending→extracting→ready→reviewed，
--      导入即建档、异步填充、下游按状态判断可用性。
--
-- 生效值规则（所有读取契约统一）：同 (drawing_id, category, 归一化 key)
--   取 source_kind='verified' 优先，否则 is_active AND source_kind='auto' 中
--   confidence 最高者。重抽只 upsert auto 行（先删旧 active auto、保留 verified）。
--
-- 前置：029_drawing_extracted_info.sql。编号延续。
-- 约定：幂等（IF NOT EXISTS / ADD COLUMN IF NOT EXISTS）；含回滚注释。
-- ════════════════════════════════════════════════════════════════════════

-- ① drawing_extracted_info 增人审列（向后兼容：既有行全为 auto/active）
ALTER TABLE drawing_extracted_info
    ADD COLUMN IF NOT EXISTS source_kind VARCHAR(10) NOT NULL DEFAULT 'auto',   -- auto | verified
    ADD COLUMN IF NOT EXISTS is_active   BOOLEAN     NOT NULL DEFAULT true,      -- 被 verified 推翻的 auto 行置 false
    ADD COLUMN IF NOT EXISTS reviewed_by UUID,                                   -- 人审操作人
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,                            -- 人审时间
    ADD COLUMN IF NOT EXISTS supersedes  UUID;                                   -- verified 行 → 它修正的 auto 行 id

-- 生效值查询主路径：项目 + 类别 + 仅生效行
CREATE INDEX IF NOT EXISTS idx_dei_active
    ON drawing_extracted_info(project_id, category, source_kind, is_active);

-- ② 每图档案状态机
CREATE TABLE IF NOT EXISTS drawing_archive_status (
    drawing_id UUID PRIMARY KEY REFERENCES drawings(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    -- pending(刚导入) | extracting(抽取中) | ready(自动完成) | reviewed(人工核过)
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    extractors_done JSONB,                      -- 已完成的抽取器 {vector_text,ocr,vlm,filename}
    extraction_version INT NOT NULL DEFAULT 0,
    item_count INT NOT NULL DEFAULT 0,          -- 该图档案信息条数（生效值）
    error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_archive_status_project
    ON drawing_archive_status(project_id, status);

-- ── 回滚 ────────────────────────────────────────────────────────────────
-- DROP INDEX IF EXISTS idx_archive_status_project;
-- DROP TABLE IF EXISTS drawing_archive_status;
-- DROP INDEX IF EXISTS idx_dei_active;
-- ALTER TABLE drawing_extracted_info
--   DROP COLUMN IF EXISTS supersedes, DROP COLUMN IF EXISTS reviewed_at,
--   DROP COLUMN IF EXISTS reviewed_by, DROP COLUMN IF EXISTS is_active,
--   DROP COLUMN IF EXISTS source_kind;
