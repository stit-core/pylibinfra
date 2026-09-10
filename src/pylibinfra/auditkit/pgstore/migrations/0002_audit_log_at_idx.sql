-- transactional: false
-- CONCURRENTLY нельзя в транзакционном блоке; на наполненной audit_log обычный
-- CREATE INDEX держал бы SHARE-блокировку и останавливал запись на всё время построения.
CREATE INDEX CONCURRENTLY IF NOT EXISTS audit_log_at_idx ON audit_log (at DESC);
