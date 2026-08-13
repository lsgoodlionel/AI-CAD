-- 047: drawing_transform 记录来源 —— 一图一行而三条路径都往这里写。
--
-- 缺来源就无从清理陈旧变换:实测 S-0-20-102.04C 的轴网识别跑于 06:02,
-- 而它的变换停在 01:47(origin_x=0),下游一直在用那条过时的值;
-- 全项目 48 张 origin_x=0 且仍有轴网。但直接删有风险 ——
-- 同一行也可能是几何路径的合法产出,或**人工确认过的比例尺**。
--
-- 取值:'axes'（Phase I 轴网路径）/ 'geometry'（图面文字读比例）/
--       'manual'（人工确认端点）/ 'unknown'（迁移前的历史行,来源不可考）。
--
-- 历史行一律 'unknown' 而不猜 —— 清理只动来源相符的行,
-- 于是这 1436 条历史变换不会被误删(见 MODELING_PIPELINE_BLUEPRINT §7 约束 5)。

ALTER TABLE drawing_transform
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'unknown';

COMMENT ON COLUMN drawing_transform.source IS
    '写入来源:axes/geometry/manual/unknown。清理陈旧变换时按此限定,'
    '避免删掉其他路径的合法产出或人工确认值。';

-- 按来源统计/清理是常用查询（每图一行,索引很小）
CREATE INDEX IF NOT EXISTS idx_drawing_transform_source
    ON drawing_transform (source);
