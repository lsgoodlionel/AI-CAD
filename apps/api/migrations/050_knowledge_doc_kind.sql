-- 规范知识库：区分「规范条文 / 标准设计图集 / 教材」
--
-- 起因：本轮把 14 份识图标准资料（10 本国家建筑标准设计图集 + 4 本教材）
-- 接入知识库。它们与既有的 31 本通用规范**规范效力完全不同**：
--   · regulation 强制性/推荐性条文 —— 可直接作为审图判据引用；
--   · atlas       标准设计图集 —— 是「怎么画/怎么做」的做法，不是判据；
--   · textbook    教材 —— 是解释与教学，**任何情况下都不能当规范引用**。
--
-- 不加这一列的后果是具体的：RAG 检索会把教材里的一句话当成规范原文
-- 交给审图结论，而读的人无从分辨。

ALTER TABLE regulation_books
  ADD COLUMN IF NOT EXISTS doc_kind TEXT NOT NULL DEFAULT 'regulation';

-- 既有 31 本都是通用规范，默认值即正确，无需回填。

ALTER TABLE regulation_books
  DROP CONSTRAINT IF EXISTS regulation_books_doc_kind_check;
ALTER TABLE regulation_books
  ADD CONSTRAINT regulation_books_doc_kind_check
  CHECK (doc_kind IN ('regulation', 'atlas', 'textbook'));

COMMENT ON COLUMN regulation_books.doc_kind IS
  'regulation=规范条文（可作审图判据）/ atlas=标准设计图集（做法，非判据）'
  ' / textbook=教材（解释性，不得作为规范引用）。';

CREATE INDEX IF NOT EXISTS idx_regulation_books_doc_kind
  ON regulation_books (doc_kind, discipline);
