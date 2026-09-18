-- 052 现实合理性分析报告
--
-- 每次建模后（或人工触发）跑一遍数学/物理/几何/规范下限的规则，把结论存下来。
-- 一行一次分析：`report` 是按规则聚合并截断样例后的结果（截断量写在
-- report->'truncated' 里，计数是完整的），`counts` 单独拎出来供列表页排序。
CREATE TABLE IF NOT EXISTS model_plausibility_reports (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    model_version   integer NOT NULL DEFAULT 0,
    report          jsonb NOT NULL DEFAULT '{}'::jsonb,
    counts          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- 页面只读最新一份；按工程 + 时间倒序取
CREATE INDEX IF NOT EXISTS idx_model_plausibility_latest
    ON model_plausibility_reports (project_id, created_at DESC);
