-- 038: 人工轴线记忆(手描的线记下来,同图/同版式图纸不必重标)
--
-- 场景:自动候选线抽取(HoughLinesP)漏掉某条轴线,人手描了一条。
-- 若不记住,同一张图重开一次、或换一张同版式图纸,还得再描一遍。
--
-- 与 manual_axis_references 的区别:
--   manual_axis_references —— 已命名的轴线基准(带轴号,直接进建模参考系)
--   axis_line_memory       —— **未命名的线位置记忆**,只用于补候选,供人点选
-- 分开存是因为语义不同:前者是结论,后者是线索。

CREATE TABLE IF NOT EXISTS axis_line_memory (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    UUID REFERENCES projects(id) ON DELETE CASCADE,
    drawing_id    UUID REFERENCES drawings(id) ON DELETE CASCADE,
    direction     TEXT NOT NULL,          -- x=竖向 | y=横向
    x1_norm       REAL NOT NULL,
    y1_norm       REAL NOT NULL,
    x2_norm       REAL NOT NULL,
    y2_norm       REAL NOT NULL,
    page_aspect   REAL,                   -- 同版式图纸的分桶键
    created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    hit_count     INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE axis_line_memory IS
    '人工手描过的轴线位置记忆:补进候选线,免得同图/同版式重复标注';

CREATE INDEX IF NOT EXISTS idx_alm_drawing ON axis_line_memory(drawing_id);
CREATE INDEX IF NOT EXISTS idx_alm_aspect
    ON axis_line_memory(project_id, page_aspect, direction);
