-- ════════════════════════════════════════════════════════════════════════
-- 032_archive_scan_summary.sql — Phase F 全量扫描:每图扫描摘要
--
-- 目标:进度页要「精确到每张图的各类信息读取进度 + 内容摘要」。在
--   drawing_archive_status 增 summary JSONB,抽取时写入每类(矢量/OCR/VLM)的
--   条数 + 简短内容样例,供进度页逐图展示;extractors_done 记已完成的抽取器。
--
-- 前置:030(drawing_archive_status)。幂等(ADD COLUMN IF NOT EXISTS)。
-- ════════════════════════════════════════════════════════════════════════

ALTER TABLE drawing_archive_status
    ADD COLUMN IF NOT EXISTS summary JSONB,          -- {by_category:{...}, by_extractor:{...}, samples:[...], vlm:{...}}
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ; -- 本图扫描开始时间(算耗时/展示)

-- ── 回滚 ────────────────────────────────────────────────────────────────
-- ALTER TABLE drawing_archive_status DROP COLUMN IF EXISTS summary, DROP COLUMN IF EXISTS started_at;
