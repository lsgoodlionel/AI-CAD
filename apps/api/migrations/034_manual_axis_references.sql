-- ════════════════════════════════════════════════════════════════════════
-- 034_manual_axis_references.sql — 人工标定轴线基准(绕开 OCR 轴号瓶颈)
--
-- 背景:自动轴号识别撞到物理上限——档案 OCR 文本筛出的「轴号」位置序与数值序
--   仅 0.3% 一致(中位逆序率 0.60);改用轴号圈检测 + 圈内 OCR 后逆序率降到 0.21,
--   仍未达可用门槛 0.15,瓶颈是通用 OCR 在小图块单字符上的能力(详见
--   docs/PHASE_H_BLUEPRINT.md §9.12/§9.13)。
--
-- 方案:**人工指定少量初始轴线作为参考系**,系统在此基准上做大范围识别与传播。
--   标定时机不限:上传图纸时 / 建模过程中 / 建模完毕的修正阶段。
--
-- 存储:两点定一条轴线,坐标用**归一化页面坐标**(同除 page_h,与构件回投同口径),
--   与图纸渲染分辨率无关;可选 `spacing_to_prev_mm` 记录与上一条同向轴线的实际
--   轴距——有它即可**直接反算比例尺**(比从文字读取更可靠)。
--
-- 前置:001(projects/drawings/users)。约定:UUID 主键 + gen_random_uuid()。
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS manual_axis_references (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    drawing_id  UUID NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,                  -- 轴号:1/2/3… 或 A/B/C…
    direction   TEXT NOT NULL,                  -- x=竖向轴线(数字号) | y=横向轴线(字母号)
    x1_norm     DOUBLE PRECISION NOT NULL,      -- 归一化页面坐标(同除 page_h)
    y1_norm     DOUBLE PRECISION NOT NULL,
    x2_norm     DOUBLE PRECISION NOT NULL,
    y2_norm     DOUBLE PRECISION NOT NULL,
    spacing_to_prev_mm DOUBLE PRECISION,        -- 与上一条同向轴线的实际轴距(可选)
    note        TEXT,
    created_by  UUID,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 同一图纸内轴号唯一(同向)
CREATE UNIQUE INDEX IF NOT EXISTS uq_manual_axis_drawing_label
    ON manual_axis_references (drawing_id, direction, label);
CREATE INDEX IF NOT EXISTS idx_manual_axis_project
    ON manual_axis_references (project_id);

-- ── 回滚 ────────────────────────────────────────────────────────────────
-- DROP INDEX IF EXISTS idx_manual_axis_project;
-- DROP INDEX IF EXISTS uq_manual_axis_drawing_label;
-- DROP TABLE IF EXISTS manual_axis_references;
