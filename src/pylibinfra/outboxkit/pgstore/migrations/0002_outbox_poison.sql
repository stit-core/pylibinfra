-- forward-only: poison-обработка (attempts/last_error/dead_at) + пересборка
-- partial-индекса с `AND dead_at IS NULL`, чтобы мёртвые строки не вычитывались.
ALTER TABLE outbox ADD COLUMN attempts INT NOT NULL DEFAULT 0;
ALTER TABLE outbox ADD COLUMN last_error TEXT;
ALTER TABLE outbox ADD COLUMN dead_at TIMESTAMPTZ;

DROP INDEX IF EXISTS outbox_unpublished_idx;

CREATE INDEX outbox_unpublished_idx
    ON outbox (created_at, id) WHERE published_at IS NULL AND dead_at IS NULL;
