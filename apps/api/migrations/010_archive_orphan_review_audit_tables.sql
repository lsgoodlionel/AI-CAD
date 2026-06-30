-- ============================================================
-- Migration 010: 归档会审审查孤表（保留数据，不删除）
-- ============================================================
-- 背景：会审审查已并入 AI 审图（第 5 引擎，结果落 ai_review_issues），
--   独立 /drawing-review 模块（routers/drawing_review.py）已删除。
--   review_audit_records / review_audit_findings 仅由该独立 router 写入，
--   现不再被任何代码读写。为可逆起见采用「重命名归档」而非 DROP：
--   历史数据完整保留，仅标记弃用；日后确认无用可再单独清理。
-- 依赖：在 007/008（曾建表/扩列）之后执行。
-- 幂等：ALTER TABLE IF EXISTS（重复执行时表已改名则跳过）。
-- 注：表间外键（findings.record_id → records.id ON DELETE CASCADE）随重命名保留。
-- ============================================================

ALTER TABLE IF EXISTS review_audit_findings RENAME TO _deprecated_review_audit_findings;
ALTER TABLE IF EXISTS review_audit_records  RENAME TO _deprecated_review_audit_records;
