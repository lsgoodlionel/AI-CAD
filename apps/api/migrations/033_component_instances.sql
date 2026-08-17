-- ════════════════════════════════════════════════════════════════════════
-- 033_component_instances.sql — Phase H 实体中心装配基座(H1)
--
-- 目标:把各引擎的碎片识别产物沉淀为「带身份 + 多观测 + provenance」的构件实体,
--   取代「碎片直接堆进 scene」的旧范式,支撑:
--     ① 准确:同一构件的多视图观测相互校验、工程约束收敛;
--     ② 可追溯:每个构件精确记录来自哪张图、哪个引擎、哪条档案内容(archive_ref);
--     ③ 可核对:人审确认/否定/改类/补漏写回,构件↔图纸一一对应。
--   详见 docs/PHASE_H_BLUEPRINT.md §2/§3。
--
-- 两张表:
--   component_instances     — 全局唯一构件实体(聚合估计后的最优值 + 人审状态)
--   component_observations  — 每次「在某张图上看到它」的证据(收敛的载体)
--
-- 前置:001(projects/drawings/users)、013(project_models.version)、
--       029(drawing_extracted_info,archive_ref 外键指向)。
-- 约定:UUID 主键 + gen_random_uuid();FK ON DELETE CASCADE;reviewed_by 纯 UUID
--       (随 030);z_* 允许 NULL(严禁默认套,让「竖向真实率」可度量);含回滚注释。
-- 说明:蓝图原写「032」,实际 032 已被 archive_scan_summary 占用,顺延为 033。
-- ════════════════════════════════════════════════════════════════════════

-- ── 构件实体 ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS component_instances (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    model_version INT  NOT NULL DEFAULT 0,        -- 对应 project_models.version(每次重建 +1)
    building_key  TEXT NOT NULL DEFAULT '',       -- 单体分组
    semantic_key  TEXT,                           -- 人类可读稳定标识,如 "col:unitA:C-3"
    type          TEXT NOT NULL,                  -- column|wall|beam|slab|pile|door|window|pipe|equipment
    grid_ref      TEXT,                           -- 轴网定位 "C-3"(数据关联主键来源)
    outline_m     JSONB,                          -- 配准后米坐标轮廓 [[x,y],...]
    z_bottom_m    DOUBLE PRECISION,               -- 起始标高(米);NULL=未知,严禁默认套
    z_top_m       DOUBLE PRECISION,               -- 顶标高(米);NULL=未知
    z_source      TEXT,                           -- section|elevation|story_default|null(provenance)
    section_json  JSONB,                          -- 截面 {w,h,d}(可来自构件表)
    type_label    TEXT,                           -- OCR/构件表反哺(钢立柱/幕墙/围护桩)
    review_state  TEXT NOT NULL DEFAULT 'auto',   -- auto|confirmed|rejected|conflict
    confidence    DOUBLE PRECISION NOT NULL DEFAULT 0,  -- 随观测数/一致性/人审上升
    reviewed_by   UUID,                           -- 人审操作人(随 030,纯 UUID 不 FK)
    reviewed_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ci_project_version
    ON component_instances(project_id, model_version);
CREATE INDEX IF NOT EXISTS idx_ci_project_type
    ON component_instances(project_id, type);
-- 语义 id 在同一次建模内唯一(允许 NULL);跨重建按 model_version 隔离
CREATE UNIQUE INDEX IF NOT EXISTS uq_ci_semantic
    ON component_instances(project_id, model_version, semantic_key)
    WHERE semantic_key IS NOT NULL;

-- ── 构件观测(证据链;构件↔图纸一一对应就落在这里)────────────────────────
CREATE TABLE IF NOT EXISTS component_observations (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instance_id  UUID NOT NULL REFERENCES component_instances(id) ON DELETE CASCADE,
    drawing_id   UUID NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
    view_type    TEXT,                            -- plan|section|elevation|detail
    engine       TEXT NOT NULL,                   -- rule|circle|yolo|spotting|ocr|vlm|human
    grid_cell    TEXT,                            -- 该观测落在的轴网格 "C-3"(关联主键)
    local_coord  JSONB,                           -- 该图页面坐标(pt)
    world_coord  JSONB,                           -- pt_to_meter 后米坐标(无 transform 时为 NULL)
    archive_ref  UUID REFERENCES drawing_extracted_info(id) ON DELETE SET NULL,  -- 溯到「具体内容」
    confidence   DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_obs_instance ON component_observations(instance_id);
CREATE INDEX IF NOT EXISTS idx_obs_drawing  ON component_observations(drawing_id);

-- ── 回滚 ────────────────────────────────────────────────────────────────
-- DROP INDEX IF EXISTS idx_obs_drawing;
-- DROP INDEX IF EXISTS idx_obs_instance;
-- DROP TABLE IF EXISTS component_observations;
-- DROP INDEX IF EXISTS uq_ci_semantic;
-- DROP INDEX IF EXISTS idx_ci_project_type;
-- DROP INDEX IF EXISTS idx_ci_project_version;
-- DROP TABLE IF EXISTS component_instances;
