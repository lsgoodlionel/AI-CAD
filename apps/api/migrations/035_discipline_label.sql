-- 035: 图纸专业细分标签(取自图框「专业」栏)
--
-- 背景:专业原先靠文件名/标题关键词猜,实测大量错判(地质剖面/围护体图 → general,
-- 卫生间排水详图 → general)。图框「专业」栏是设计单位填写的权威值,且已在档案层
-- (drawing_extracted_info,带 bbox)可读,不必重新 OCR。
--
-- 为什么新增列而不改 discipline 取值:discipline 是粗粒度枚举,规则引擎/建模按它
-- 选型(structure.yaml / mep.yaml…),直接写入「给排水」会破坏选型。故:
--   discipline_label —— 图框实读专业(给排水/基坑围护/电气…),用于显示与筛选
--   discipline       —— 保持粗粒度枚举,由 label 映射修正

ALTER TABLE drawings ADD COLUMN IF NOT EXISTS discipline_label TEXT;

COMMENT ON COLUMN drawings.discipline_label IS
    '图框「专业」栏实读值(给排水/基坑围护/电气…);NULL = 未读到,回落 discipline';

CREATE INDEX IF NOT EXISTS idx_drawings_discipline_label
    ON drawings(project_id, discipline_label);
