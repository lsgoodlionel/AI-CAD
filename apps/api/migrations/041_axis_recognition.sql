-- 041 轴网识别结果(Phase I 接入系统)
--
-- 此前 Phase I 的识别链路只在一次性脚本里跑通,结果没有落点:
--   ① 分区**编号**(§8.0.5 未规定哪个分区是 1)推不出来,脚本里是硬编或凑真值的;
--   ② RANSAC 判出的**粗错**(OCR 误读坐标)只打印在 stdout,没人看得到;
--   ③ 国标校验的违规同样没有去处。
-- 这三样都需要人看一眼再定,所以必须有表存、有接口读、有界面确认。
--
-- 一图一行:识别是幂等的,重跑覆盖即可;人工确认过的分区号单独保留,不被覆盖。

CREATE TABLE IF NOT EXISTS axis_recognition (
    drawing_id        UUID PRIMARY KEY REFERENCES drawings(id) ON DELETE CASCADE,
    project_id        UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status            TEXT NOT NULL DEFAULT 'pending',   -- pending|running|ready|failed
    page_w            DOUBLE PRECISION,
    page_h            DOUBLE PRECISION,
    circle_count      INT NOT NULL DEFAULT 0,
    additional_count  INT NOT NULL DEFAULT 0,            -- §8.0.6 分数式附加轴线
    axis_count        INT NOT NULL DEFAULT 0,
    zones             JSONB,   -- [{index, numeric_axes, alpha_axes, extent}]
    axes              JSONB,   -- [{label, label_kind, angle_deg, offset_pt, zone_index, ...}]
    anchors           JSONB,   -- 已写入 axis_intersections 的轴号对锚点
    outliers          JSONB,   -- **粗错清单**:页面位置 + OCR 读到的值,等人工核对
    violations        JSONB,   -- 国标校验(§8.0.3~8.0.6)违规
    transform         JSONB,   -- {scale_m_pt, rotation_deg, rmse_m, reflect}
    error             TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 人工确认的分区编号。**与识别结果分表**:重跑识别不能把人确认过的号冲掉
-- (这正是 E1.5 档案层「auto/verified 分离」的同一条经验)。
CREATE TABLE IF NOT EXISTS axis_zone_confirmation (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    drawing_id    UUID NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
    zone_index    INT NOT NULL,          -- 识别结果里的分区下标(按规模降序)
    zone_label    TEXT NOT NULL,         -- 人工确认的分区号,如 "1" / "2"
    confirmed_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    confirmed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (drawing_id, zone_index)
);

CREATE INDEX IF NOT EXISTS idx_axis_recognition_project
    ON axis_recognition(project_id, status);
CREATE INDEX IF NOT EXISTS idx_axis_zone_confirmation_drawing
    ON axis_zone_confirmation(drawing_id);

COMMENT ON TABLE axis_recognition IS
    'Phase I 轴网识别结果(圈→带→分区→轴号→坐标锚点)。一图一行,重跑覆盖。';
COMMENT ON COLUMN axis_recognition.outliers IS
    'RANSAC 判定的粗错坐标标注,必须交人工核对——错的世界坐标比缺锚点危险得多。';
COMMENT ON TABLE axis_zone_confirmation IS
    '人工确认的分区编号。§8.0.5 未规定编号顺序,几何推不出,每个分区确认一次。';

-- 回滚:
-- DROP TABLE IF EXISTS axis_zone_confirmation;
-- DROP TABLE IF EXISTS axis_recognition;
