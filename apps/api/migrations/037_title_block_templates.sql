-- 037: 图框字段区域记忆库(人工框一次 → 记住 → 自动套用同版式图纸)
--
-- 按标签找值对付得了大多数图,但**标签本身没被 OCR 出来**的图彻底读不到(实测 140 张)。
-- 人在图上框一次那个区域,系统记住,便可自动套用到同版式的其他图纸,并跨项目复用。
--
-- project_id 为 NULL = 全局记忆(跨项目);查找时本项目优先,其次全局按命中次数排序。
-- 坐标统一除以页高归一化,与 drawing_transform / manual_axis_references 同口径。

CREATE TABLE IF NOT EXISTS title_block_templates (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id        UUID REFERENCES projects(id) ON DELETE CASCADE,
    field             TEXT NOT NULL,          -- discipline | drawing_no | title
    x1                REAL NOT NULL,
    y1                REAL NOT NULL,
    x2                REAL NOT NULL,
    y2                REAL NOT NULL,
    page_aspect       REAL,                   -- 页面宽高比:同版式图框的分桶键
    source_drawing_id UUID REFERENCES drawings(id) ON DELETE SET NULL,
    created_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    hit_count         INT NOT NULL DEFAULT 0, -- 命中次数:用得越多排得越前
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at      TIMESTAMPTZ
);

COMMENT ON TABLE title_block_templates IS
    '图框字段区域记忆:人工框选一次后自动套用到同版式图纸,可跨项目复用';
COMMENT ON COLUMN title_block_templates.project_id IS 'NULL = 全局记忆(跨项目可用)';
COMMENT ON COLUMN title_block_templates.page_aspect IS '页面宽高比,同版式图框的分桶键';

CREATE INDEX IF NOT EXISTS idx_tbt_lookup
    ON title_block_templates(field, page_aspect, hit_count DESC);
CREATE INDEX IF NOT EXISTS idx_tbt_project
    ON title_block_templates(project_id, field);
