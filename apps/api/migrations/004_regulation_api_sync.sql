-- Migration 004: 规范 API 定时同步支持字段
-- 为 regulation_api_sources 添加同步结果字段
-- 为 regulation_books 添加 api_source_id 外键（支持 API 数据源关联）
-- 为 regulation_articles 添加 embedding 字段（向量化状态标记）
-- 为 regulation_articles 添加 chapter_no 字段（章节编号）

ALTER TABLE regulation_api_sources
    ADD COLUMN IF NOT EXISTS last_sync_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_sync_error TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

ALTER TABLE regulation_books
    ADD COLUMN IF NOT EXISTS api_source_id UUID REFERENCES regulation_api_sources(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

ALTER TABLE regulation_articles
    ADD COLUMN IF NOT EXISTS embedding BOOLEAN DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS chapter_no VARCHAR(50),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_books_api_source ON regulation_books (api_source_id);
CREATE INDEX IF NOT EXISTS idx_articles_embedding ON regulation_articles (book_id) WHERE embedding IS NULL;

COMMENT ON COLUMN regulation_api_sources.last_sync_count IS '最近一次同步写入/更新的条文数';
COMMENT ON COLUMN regulation_api_sources.last_sync_error IS '最近一次同步失败原因，成功后清空';
COMMENT ON COLUMN regulation_books.api_source_id IS '若为 API 同步创建，关联数据源 ID';
COMMENT ON COLUMN regulation_articles.embedding IS 'NULL=未向量化，TRUE=已向量化';
