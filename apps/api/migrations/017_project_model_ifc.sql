-- ============================================================
-- Migration 017: project_models — IFC / Fragments 构建元数据
-- ============================================================
-- 蓝图来源：docs/MODEL_BASE_BLUEPRINT.md 第 4/7 节；任务 docs/PHASE_A_TASKS.md A-05。
-- 依赖：013_project_models.sql（project_models 表）。
-- 可重复执行（IF NOT EXISTS）；向后兼容（新列可空，缺省不报错）。
--
-- 设计决策（A-05 优先 JSON，避免频繁 DDL）：
--   project_models.scene 为 JSONB，已承载全部结构化模型数据（floors/markers/
--   ifc_models/... 见 013、014 迁移与 services/model_builder.py build_scene）。
--   IFC/Fragments 元数据同样以 JSON 契约挂在 scene 内，避免为每个新字段加列：
--
--     scene.model_ifc = {
--       "ifc_key":      str,                       -- MinIO 中程序化 IFC 的 key
--       "frag_key":     str | null,                -- Fragments (.frag) 产物 key，转换失败为 null
--       "build_mode":   "ifc" | "elements" | "texture",
--       "is_estimated": bool,                      -- 楼层标高是否为估算（Phase A 恒 true；
--                                                  --   Phase B 跨图 Z 恢复后转 false）
--       "generated_at": str                        -- ISO8601
--     }
--
--   本迁移仅新增一个「可查询汇总列」build_mode，用于按渲染模式过滤/统计项目模型
--   （例如看板筛选已产出 IFC 的项目），无需对 scene JSONB 做深层扫描。
--   build_mode 是 scene.model_ifc.build_mode 的物化冗余，权威值仍以 scene JSON 为准；
--   写入方（tasks/model_build.py）在落库 scene 时同步刷新该列。旧行该列为 NULL，
--   读取端应把 NULL 视为「未知 / 沿用 scene 内取值或回退 texture」。
-- ============================================================

ALTER TABLE project_models
    ADD COLUMN IF NOT EXISTS build_mode VARCHAR(16);

COMMENT ON COLUMN project_models.build_mode IS
    'scene.model_ifc.build_mode 的可查询汇总冗余：ifc|elements|texture；NULL=未知/旧数据。权威值见 scene JSON。';

-- 仅索引已知模式的行（NULL 不入索引），支撑「按渲染模式筛选项目模型」查询。
CREATE INDEX IF NOT EXISTS idx_project_models_build_mode
    ON project_models(build_mode)
    WHERE build_mode IS NOT NULL;

-- ============================================================
-- 回滚（ROLLBACK / DOWN）：手动执行以撤销本迁移
-- ============================================================
-- DROP INDEX IF EXISTS idx_project_models_build_mode;
-- ALTER TABLE project_models DROP COLUMN IF EXISTS build_mode;
-- 说明：scene.model_ifc 为 JSON 契约，不涉及 schema，回滚后仍可读取（前端/后端按缺省处理）。
-- ============================================================
