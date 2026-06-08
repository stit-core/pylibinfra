CREATE TABLE audit_log (
    id          BIGSERIAL    PRIMARY KEY,
    actor_id    TEXT,
    action      TEXT         NOT NULL,
    resource    TEXT         NOT NULL,
    resource_id TEXT,
    metadata    JSONB,
    ip          TEXT,
    request_id  TEXT,
    at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX audit_log_actor_idx ON audit_log (actor_id, at DESC);
CREATE INDEX audit_log_resource_idx ON audit_log (resource, resource_id, at DESC);

-- append-only на уровне БД: триггер роняет UPDATE/DELETE — аудит нельзя править/чистить
-- задним числом даже скомпрометированной app-ролью (INSERT-only остаётся доступен).
CREATE FUNCTION audit_log_no_mutate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % forbidden', TG_OP;
END;
$$;
CREATE TRIGGER audit_log_block_mutate
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_no_mutate();
