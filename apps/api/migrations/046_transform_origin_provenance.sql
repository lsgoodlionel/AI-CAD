-- 变换原点的 provenance：该方向**没有检出轴线**时原点按 0 兜底，
-- 但那不是「原点真在 0」—— 0 是个合法坐标值，下游无从分辨。
--
-- 实测(2026-08-12,1436 条变换):
--   origin_x = 0 : 72 张
--   origin_y = 0 : 77 张   合计 10.4%
--   两方向都为 0 : **0 张**  ⇒ 缺的都是**一个**方向,与「轴网非双向」吻合
--
-- 个案 S-0-20-102.04C:变换正常(1:150、置信 1.00)但 origin_x=0.0
-- (正常图如 A-01-02A 是 992.1),其墙 x 落在 149~2356 米,
-- 把 F1 层构件包络撑到 2207 米。
--
-- 与 drawing_transform 的 1:335 万教训同源:
-- **一个「看起来合法」的值比缺失更危险** —— 缺失让下游降级,假值一路通行。
ALTER TABLE drawing_transform
    ADD COLUMN IF NOT EXISTS origin_x_estimated BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS origin_y_estimated BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN drawing_transform.origin_x_estimated IS
    'true = x 方向没检出轴线,原点按 0 兜底(非实测);该方向坐标会整体偏移';
COMMENT ON COLUMN drawing_transform.origin_y_estimated IS
    'true = y 方向没检出轴线,原点按 0 兜底(非实测)';
