-- 039: 轴线交叉点 + 工程坐标原点(交叉点定位整张图纸)
--
-- 需求:
--   ④ 多张图出现相同轴号时,用 ≥2 个同名交叉点把整张图摆到同一坐标系;
--   ⑤ 交叉点可填工程坐标 XYZ;一套图先在某张图定义 (0,0,0),**每个专业各定义一次**
--      ——各专业的图往往用不同局部原点,共用一个会整体错位。
--
-- 交叉点的身份是「轴号对」(label_x × label_y,如 1×A),这正是跨图对齐的天然锚点。

CREATE TABLE IF NOT EXISTS axis_intersections (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    drawing_id    UUID NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
    label_x       TEXT NOT NULL,          -- 竖向轴号,如 1 / 1-1
    label_y       TEXT NOT NULL,          -- 横向轴号,如 A / A-2
    x_norm        REAL NOT NULL,          -- 图上归一化坐标(同除页高)
    y_norm        REAL NOT NULL,
    world_x       DOUBLE PRECISION,       -- 工程坐标(米),可空
    world_y       DOUBLE PRECISION,
    world_z       DOUBLE PRECISION,
    note          TEXT,
    created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (drawing_id, label_x, label_y)
);

COMMENT ON TABLE axis_intersections IS
    '轴线交叉点:身份=轴号对,带可选工程坐标,用于跨图对齐与整图世界定位';

CREATE INDEX IF NOT EXISTS idx_axis_int_drawing ON axis_intersections(drawing_id);
CREATE INDEX IF NOT EXISTS idx_axis_int_labels
    ON axis_intersections(project_id, label_x, label_y);

-- 每专业一个工程坐标原点(0,0,0 的定义位置)
CREATE TABLE IF NOT EXISTS project_coordinate_origins (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    discipline      TEXT NOT NULL,        -- 图框实读专业(建筑/结构/给排水…)
    drawing_id      UUID REFERENCES drawings(id) ON DELETE SET NULL,
    intersection_id UUID REFERENCES axis_intersections(id) ON DELETE SET NULL,
    note            TEXT,
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, discipline)
);

COMMENT ON TABLE project_coordinate_origins IS
    '每专业的工程坐标原点(0,0,0)定义在哪张图的哪个交叉点上';
