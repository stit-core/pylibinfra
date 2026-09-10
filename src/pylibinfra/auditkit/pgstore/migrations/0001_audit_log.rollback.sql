DROP TRIGGER IF EXISTS audit_log_block_mutate ON audit_log;
DROP FUNCTION IF EXISTS audit_log_no_mutate();
DROP TABLE IF EXISTS audit_log;
