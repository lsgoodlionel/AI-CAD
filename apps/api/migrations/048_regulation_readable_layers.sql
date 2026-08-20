-- 规范知识库的「人机双读」三层
--
-- 需求：规范库要同时满足机器和人类阅读——
--   ① 可阅读的 PDF 原件（人看原版排版、图表、签章）
--   ② 被识别的文字版本全文（人核对识别质量、机器做全文检索）
--   ③ 整理处理过的单个条文（系统消费：审图、图谱推理、向量检索）
--
-- 此前只有第 ③ 层。31 本书 `file_key` 全为 NULL（导入脚本从本地直读、
-- 没上传 MinIO），全文也从未保留——识别完切成条文就丢了，
-- 于是「识别得对不对」无从人工核对。

ALTER TABLE regulation_books
  ADD COLUMN IF NOT EXISTS full_text      TEXT,
  ADD COLUMN IF NOT EXISTS text_chars     INTEGER,
  ADD COLUMN IF NOT EXISTS page_count     INTEGER,
  -- text_layer | ocr | docling —— 人核对时要知道这份文字怎么来的，
  -- OCR 出来的和 PDF 文本层直接取的可信度完全不同。
  ADD COLUMN IF NOT EXISTS extract_method TEXT;

COMMENT ON COLUMN regulation_books.full_text IS
  '识别出的全文（第②层）。保留原始换行，供人工核对与全文检索。';
COMMENT ON COLUMN regulation_books.extract_method IS
  'text_layer=PDF 文本层直取 / ocr=扫描件识别 / docling=版面解析。'
  '人工核对时要知道这份文字的来路——OCR 与文本层直取的可信度不同。';

CREATE INDEX IF NOT EXISTS idx_regulation_books_fulltext
  ON regulation_books USING gin (to_tsvector('simple', coalesce(full_text, '')));
