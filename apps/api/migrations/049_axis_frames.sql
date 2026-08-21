-- Phase K-2：工程自有坐标系（轴网帧）的持久化
--
-- K-1 实测（`docs/PHASE_K_BLUEPRINT.md` §7）：二维联合聚类后
-- 大歌剧院 **86%** 的图落在有交叉约束的帧里、残差中位 0.1 毫米、
-- P95 17 厘米；轨道交通 53%。
--
-- **为什么需要它**：实测有世界坐标的图只有 0.5%（轨道交通 0），
-- 而有轴号的图 100%/95%。世界坐标近乎没有不是数据质量问题，
-- 是施工图的固有属性——国标不要求每张图标测量坐标，
-- 而定位轴线是每张平面图的必备要素（GB/T 50001 §8）。
-- 轴网帧让每张图有共同参照物，全程不需要一个测量坐标。

CREATE TABLE IF NOT EXISTS axis_frames (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    -- 分组键：帧只在同一层同一单体内成立。**分组是必要条件不是优化项**
    -- ——实测不分组时 96% 的图残差超阈（§6）。
    story_key     TEXT NOT NULL,
    building_unit TEXT NOT NULL DEFAULT '-',
    -- 同一分组内可能有多套互不相容的轴网（分区工程一图三套，§8.33），
    -- 所以帧要编号；0 号是成员最多的主轴网。
    frame_index   INTEGER NOT NULL DEFAULT 0,
    -- {"x": {轴号: 米}, "y": {轴号: 米}}，原点为各方向编号最小的轴
    axes          JSONB NOT NULL,
    member_count  INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, story_key, building_unit, frame_index)
);

CREATE TABLE IF NOT EXISTS drawing_frame_placements (
    drawing_id  UUID PRIMARY KEY REFERENCES drawings(id) ON DELETE CASCADE,
    frame_id    UUID NOT NULL REFERENCES axis_frames(id) ON DELETE CASCADE,
    -- 加到该图坐标上即落入帧内（米）
    offset_x    DOUBLE PRECISION NOT NULL,
    offset_y    DOUBLE PRECISION NOT NULL,
    -- 对齐残差（米）。**「这张图能不能信」的唯一依据**，
    -- 下游摆放构件时按它决定信到什么程度。
    residual_m  DOUBLE PRECISION,
    -- 该帧的成员数。**单成员帧没有交叉约束**——一张图自己跟自己对齐，
    -- 残差当然是 0，却不构成任何证据。消费方要能区分。
    frame_size  INTEGER NOT NULL DEFAULT 1,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_axis_frames_project
    ON axis_frames (project_id, story_key, building_unit);
CREATE INDEX IF NOT EXISTS idx_frame_placements_frame
    ON drawing_frame_placements (frame_id);

COMMENT ON COLUMN drawing_frame_placements.frame_size IS
  '所在帧的成员数。单成员帧无交叉约束（残差恒 0 但不构成证据），'
  '消费方须按它折算可信度——「进帧率」用它才不会灌水。';
