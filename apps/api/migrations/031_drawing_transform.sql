-- ════════════════════════════════════════════════════════════════════════
-- 031_drawing_transform.sql — Phase E 路径C-A1 每图坐标变换持久化
--
-- 目标:让「图纸信息档案」里页面点(pt)坐标的信息(轴号/文字位置)能转成
--   3D 模型的米坐标。变换 = element_recognizer._Ctx.to_m 的三要素:
--   比例尺 scale(米/点)+ 轴网原点 origin(pt)+ 页高 page_h(pt),
--   公式:x_m=(x_pt-origin_x)*scale, y_m=((page_h-y_pt)-origin_y)*scale。
--
-- 这三要素只在 recognize() 内部算、原先不落库,导致档案 pt 坐标无法进 3D
-- (轴号→3D、OCR 文字反哺分类都卡在此)。抽取时复用识别器的检测函数算出并落库。
--
-- 前置:029/030(drawing_extracted_info / archive)。编号延续。
-- 约定:每图一行(drawing_id PK);幂等(ON CONFLICT 覆盖);含回滚注释。
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS drawing_transform (
    drawing_id UUID PRIMARY KEY REFERENCES drawings(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scale_m_pt DOUBLE PRECISION NOT NULL,   -- 米/点
    origin_x   DOUBLE PRECISION NOT NULL,   -- 轴网原点 x(pt)
    origin_y   DOUBLE PRECISION NOT NULL,   -- 轴网原点 y(pt)
    page_h     DOUBLE PRECISION NOT NULL,   -- 页高(pt)
    confidence DOUBLE PRECISION,            -- 比例尺/轴网检测置信(轴号占比)
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_drawing_transform_project
    ON drawing_transform(project_id);

-- ── 回滚 ────────────────────────────────────────────────────────────────
-- DROP INDEX IF EXISTS idx_drawing_transform_project;
-- DROP TABLE IF EXISTS drawing_transform;
