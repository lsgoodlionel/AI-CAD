-- 036: 图纸目录顺序(默认排序 = 目录在前,其余按目录顺序)
--
-- 排序三层(见 services/drawing_directory.py):
--   sort_rank 0 目录图本身 → 1 目录列出的图(按 directory_seq) → 2 其余(按 sort_key)
-- 第 3 层用「专业 + 图号自然序」兜底,因为目录图 OCR 稀疏(实测仅覆盖约 8% 图纸),
-- 只靠目录会让绝大多数图纸乱序。

ALTER TABLE drawings ADD COLUMN IF NOT EXISTS sort_rank INT NOT NULL DEFAULT 2;
ALTER TABLE drawings ADD COLUMN IF NOT EXISTS directory_seq INT;
ALTER TABLE drawings ADD COLUMN IF NOT EXISTS directory_sheet_id UUID;
ALTER TABLE drawings ADD COLUMN IF NOT EXISTS sort_key TEXT;

COMMENT ON COLUMN drawings.sort_rank IS '排序层级:0=目录图 1=目录列出 2=目录未覆盖';
COMMENT ON COLUMN drawings.directory_seq IS '在图纸目录中的全局顺序;NULL=目录未列出';
COMMENT ON COLUMN drawings.directory_sheet_id IS '列出本图的那张目录图';
COMMENT ON COLUMN drawings.sort_key IS '兜底排序键:专业 + 图号自然序';

CREATE INDEX IF NOT EXISTS idx_drawings_default_order
    ON drawings(project_id, sort_rank, directory_seq NULLS LAST, sort_key);
CREATE INDEX IF NOT EXISTS idx_drawings_directory_sheet
    ON drawings(directory_sheet_id);
