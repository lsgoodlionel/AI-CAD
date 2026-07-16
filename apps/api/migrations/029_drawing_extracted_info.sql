-- ════════════════════════════════════════════════════════════════════════
-- 029_drawing_extracted_info.sql — Phase E 泳道 E1 工程信息模块数据基座
--
-- 目标：把既有抽取器（DXF/PDF 矢量文字、OCR、轴网、剖面标高、文件名/图签、VLM）
--   的产物统一持久化——此前全部仅内存中转，工程信息模块无处可读历史结果。
--   每条信息强制携带 drawing_id 溯源（「每一个信息都要链接来源图纸」）。
--
-- 范式：照 model_semantic_evidence（migration 016）的溯源模型
--   （drawing_id + 原文 + 解析值 + 抽取器 + 置信度 + 位置）。
-- 消费方：routers/project_info.py（按项目聚合/分页/搜索）、
--   工程模型轴网层（E2，category='axis'）、OCR/VLM 评测（E4）。
--
-- 前置：001（projects/drawings）。编号延续 019–028。
-- 约定：硬 FK 级联删除（图纸删则信息删）；幂等（IF NOT EXISTS）；含回滚注释。
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS drawing_extracted_info (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    drawing_id UUID NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,

    -- 信息类别：与 core/model3d/ocr TokenKind 词汇对齐 + 扩展项
    --   elevation | axis | dimension | level_name | room_name | note | title
    --   | title_block（图签字段）| design_note（整页设计说明,E1 后续）| other
    category VARCHAR(40) NOT NULL,

    content TEXT NOT NULL,          -- 原文（识别/解析出的文字本体）
    value_json JSONB,               -- 解析值：{"elevation_m":..}|{"dim_mm":..}|{"label","coord","dir"}|{图签字段}
    location_json JSONB,            -- 位置：{"x","y"} 或 {"bbox":[x0,y0,x1,y1]}（页面点，左上原点）

    -- 抽取器来源：vector_text（DXF TEXT/MTEXT 或 PDF 矢量文字）| ocr
    --   | grid_anchor | section_level | filename | vlm
    extractor VARCHAR(40) NOT NULL,
    confidence NUMERIC(5, 4),       -- 0~1；确定性来源（vector_text/filename）可为 NULL=确定

    extraction_version INT NOT NULL DEFAULT 1,   -- 重抽代次（覆盖式重建时 +1）
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 项目级按类别聚合（工程信息页主查询路径）
CREATE INDEX IF NOT EXISTS idx_dei_project_category
    ON drawing_extracted_info(project_id, category);

-- 单图删除/重抽路径
CREATE INDEX IF NOT EXISTS idx_dei_drawing
    ON drawing_extracted_info(drawing_id);

-- 项目内全文检索兜底（ILIKE 前缀过滤足够 V1；量级不需 GIN/tsvector）
CREATE INDEX IF NOT EXISTS idx_dei_project_extractor
    ON drawing_extracted_info(project_id, extractor);

-- ── 回滚 ────────────────────────────────────────────────────────────────
-- DROP INDEX IF EXISTS idx_dei_project_extractor;
-- DROP INDEX IF EXISTS idx_dei_drawing;
-- DROP INDEX IF EXISTS idx_dei_project_category;
-- DROP TABLE IF EXISTS drawing_extracted_info;
