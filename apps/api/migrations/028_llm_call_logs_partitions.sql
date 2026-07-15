-- ============================================================
-- Migration 028: llm_call_logs 分区维护兜底（运维健壮性）
-- ============================================================
-- 背景：llm_call_logs 按月 RANGE 分区（见 002_model_management.sql），
-- 但缺少「未来月份分区自动创建」机制。一旦当月分区缺失，该月的 LLM 调用
-- 日志 INSERT 直接抛
--   CheckViolationError: no partition of relation "llm_call_logs" found for row
-- 打断整条可观测性写入（本地已复现 2026-07 缺分区）。
--
-- 双保险修复：
--   ① 本迁移建 DEFAULT 兜底分区 —— 任何时刻漏建正式月分区都不会崩，
--      未匹配的行落入 default 分区，仍可查询（只是失去按月裁剪优化）。
--   ② 幂等补建当月 + 未来两月的正式分区（IF NOT EXISTS，可反复执行）。
--   ③ 正式月分区由 Celery beat 任务 tasks.partition_maintenance 每月滚动建。
--
-- 顺序要点：先建正式月分区，最后建 DEFAULT。
-- 若先有 DEFAULT 再建具体月分区，PG 需扫描 DEFAULT 确认无冲突行；
-- 此处正式月分区先落地，DEFAULT 最后兜住「其余一切」，无冲突。
-- ============================================================

-- ① 幂等补建：当月及未来两月（覆盖 002 迁移遗漏的 2026-07 起）
CREATE TABLE IF NOT EXISTS llm_call_logs_2026_07 PARTITION OF llm_call_logs
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS llm_call_logs_2026_08 PARTITION OF llm_call_logs
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE IF NOT EXISTS llm_call_logs_2026_09 PARTITION OF llm_call_logs
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

-- ② DEFAULT 兜底分区：漏建任何正式月分区时接住写入，避免 CheckViolationError
CREATE TABLE IF NOT EXISTS llm_call_logs_default PARTITION OF llm_call_logs DEFAULT;
