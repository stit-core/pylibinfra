DROP INDEX IF EXISTS outbox_unpublished_idx;

CREATE INDEX outbox_unpublished_idx
    ON outbox (created_at, id) WHERE published_at IS NULL;

ALTER TABLE outbox DROP COLUMN dead_at;
ALTER TABLE outbox DROP COLUMN last_error;
ALTER TABLE outbox DROP COLUMN attempts;
