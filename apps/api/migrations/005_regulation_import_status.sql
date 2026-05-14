-- Migration 005: regulation PDF import status support

ALTER TABLE regulation_books
  DROP CONSTRAINT IF EXISTS regulation_books_status_check;

ALTER TABLE regulation_books
  ADD CONSTRAINT regulation_books_status_check
  CHECK (status IN ('draft','processing','active','import_failed','superseded','withdrawn'));

