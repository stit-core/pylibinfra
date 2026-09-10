"""Интеграционные тесты outboxkit/pgstore против реального Postgres."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import logkit
from pylibinfra.clockkit import Fixed
from pylibinfra.natskit import StreamBus
from pylibinfra.outboxkit import (
    BusyError,
    Config,
    Failure,
    MessageRef,
    NoTxError,
    Outbox,
    OutboxError,
    OutboxInstanceProvider,
    Publisher,
    PublishMode,
    Repository,
    ReviveFilter,
    new_outbox,
    new_stream_transport,
    pgstore,
)
from pylibinfra.outboxkit.pgstore import (
    MIGRATIONS,
    PendingRowsError,
    Store,
    partition_sql,
    table_migrations,
)
from pylibinfra.pgkit import DB, Migrated, Transactor, migrate_up_all
from pylibinfra.pgkit.types import Pool
from pylibinfra.testkit import PostgresFixture, natstest, start_postgres
from pylibinfra.testkit.logtest import Capture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_INSTANCE = "outbox"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg() -> AsyncIterator[PostgresFixture]:
    async with start_postgres(migrations=[MIGRATIONS]) as fixture:
        yield fixture


@pytest_asyncio.fixture(loop_scope="module")
async def pool(pg: PostgresFixture) -> Pool:
    return await pg.pool()


@pytest_asyncio.fixture(loop_scope="module")
async def db(pg: PostgresFixture) -> DB:
    return await pg.db()


@pytest_asyncio.fixture(loop_scope="module")
async def tx(pg: PostgresFixture) -> Transactor:
    return await pg.transactor()


@pytest_asyncio.fixture(loop_scope="module", autouse=True)
async def _clean(pool: Pool) -> None:
    await pool.execute("TRUNCATE outbox")


class _PgAppProvider(Provider):
    scope = Scope.APP

    def __init__(self, db: DB, tx: Transactor, recorder: natstest.Recorder | None = None) -> None:
        super().__init__()
        self._db = db
        self._tx = tx
        self._recorder = recorder or natstest.new()

    @provide
    def database(self) -> DB:
        return self._db

    @provide
    def transactor(self) -> Transactor:
        return self._tx

    @provide
    def migrated(self) -> Migrated:
        return Migrated()

    @provide
    def store_config(self) -> pgstore.Config:
        return pgstore.Config(require_tx=False)

    @provide
    def config(self) -> Config:
        return Config()

    @provide
    def logger(self) -> logkit.Logger:
        return Capture()

    @provide
    def stream_bus(self) -> StreamBus:
        return self._recorder


def _store(db: DB, **kwargs: object) -> Store:
    return Store(db, require_tx=False, **kwargs)  # type: ignore[arg-type]


async def _insert(tx: Transactor, store: Store, subject: str, payload: dict[str, int]) -> None:
    async def write() -> None:
        await store.insert(subject, json.dumps(payload).encode())

    await tx.with_tx(write)


async def test_insert_list_mark_round_trip(db: DB, tx: Transactor) -> None:
    store = _store(db)
    await _insert(tx, store, "order.created", {"id": 1})

    async def drain() -> None:
        msgs = await store.list_unpublished(10, PublishMode.BATCH)
        assert len(msgs) == 1
        assert msgs[0].subject == "order.created"
        assert json.loads(msgs[0].payload) == {"id": 1}
        assert msgs[0].attempts == 0
        assert msgs[0].created_at.tzinfo is not None
        await store.mark_published([msgs[0].ref()])

    await tx.with_tx(drain)

    published = await db.fetchval(
        "SELECT published_at FROM outbox WHERE subject = $1", "order.created"
    )
    assert published is not None
    assert await _list(tx, store) == []


async def test_rollback_removes_outbox_row(pool: Pool, db: DB, tx: Transactor) -> None:
    store = _store(db)

    async def write_and_fail() -> None:
        await store.insert("order.created", b'{"id":2}')
        raise RuntimeError("domain failed")

    with pytest.raises(RuntimeError, match="domain failed"):
        await tx.with_tx(write_and_fail)

    assert await pool.fetchval("SELECT count(*) FROM outbox") == 0


async def test_require_tx_guard_rejects_insert_outside_transaction(db: DB, tx: Transactor) -> None:
    store = Store(db, require_tx=True)

    with pytest.raises(NoTxError, match="outside transaction"):
        await store.insert("order.created", b'{"id":3}')

    async def write() -> None:
        await store.insert("order.created", b'{"id":3}')

    await tx.with_tx(write)
    assert len(await _list(tx, store)) == 1


async def test_failures_record_attempts_backoff_and_dead(db: DB, tx: Transactor) -> None:
    store = _store(db)
    await _insert(tx, store, "order.created", {"id": 4})
    msgs = await _list(tx, store)

    async def fail() -> None:
        await store.record_failures(
            [Failure(ref=msgs[0].ref(), err="broker down", retry_after=600.0, dead=False)]
        )

    await tx.with_tx(fail)

    row = await db.fetchrow(
        "SELECT attempts, last_error, next_attempt_at > NOW() AS waiting FROM outbox WHERE id = $1",
        msgs[0].id,
    )
    assert row is not None
    assert row["attempts"] == 1
    assert row["last_error"] == "broker down"
    assert row["waiting"] is True
    # Строка в backoff'е не вычитывается batch-режимом.
    assert await _list(tx, store) == []
    # ordered не перескакивает голову очереди: тик просто ничего не делает.
    assert await _list(tx, store, PublishMode.ORDERED) == []

    async def due_now() -> None:
        await db.execute("UPDATE outbox SET next_attempt_at = NOW() - INTERVAL '1 second'")

    await due_now()
    assert len(await _list(tx, store)) == 1


async def test_dead_rows_leave_the_queue_and_come_back_with_revive(db: DB, tx: Transactor) -> None:
    store = _store(db)
    await _insert(tx, store, "order.created", {"id": 5})
    await _insert(tx, store, "lead.created", {"id": 6})
    msgs = await _list(tx, store)

    async def kill() -> None:
        await store.record_failures(
            [Failure(ref=m.ref(), err="poison", retry_after=0.0, dead=True) for m in msgs]
        )

    await tx.with_tx(kill)
    assert await _list(tx, store) == []

    backlog = await store.backlog()
    assert backlog == []

    revived = await store.revive_dead(ReviveFilter(subject="order.created"))
    assert revived == 1

    alive = await _list(tx, store)
    assert [m.subject for m in alive] == ["order.created"]
    assert alive[0].attempts == 0
    # Чужой subject остаётся dead.
    assert await db.fetchval("SELECT count(*) FROM outbox WHERE dead_at IS NOT NULL") == 1


async def test_backlog_reports_age_and_pending_per_subject(db: DB, tx: Transactor) -> None:
    store = _store(db)
    await _insert(tx, store, "order.created", {"id": 7})
    await _insert(tx, store, "order.created", {"id": 8})
    await _insert(tx, store, "lead.created", {"id": 9})

    backlog = {b.subject: b for b in await store.backlog()}

    assert backlog["order.created"].pending == 2
    assert backlog["lead.created"].pending == 1
    assert backlog["order.created"].oldest >= 0


async def test_ordered_head_is_busy_while_another_transaction_holds_it(
    pg: PostgresFixture, db: DB, tx: Transactor
) -> None:
    store = _store(db)
    await _insert(tx, store, "order.created", {"id": 10})
    await _insert(tx, store, "order.created", {"id": 11})

    other_pool = await pg.pool()
    holder_done = asyncio.Event()
    holding = asyncio.Event()

    async def hold() -> None:
        async with other_pool.acquire() as conn, conn.transaction():
            await conn.fetch(
                "SELECT id FROM outbox WHERE published_at IS NULL "
                "ORDER BY created_at, id LIMIT 1 FOR UPDATE"
            )
            holding.set()
            await holder_done.wait()

    task = asyncio.create_task(hold())
    await asyncio.wait_for(holding.wait(), 5)
    try:

        async def take_head() -> None:
            await store.list_unpublished(10, PublishMode.ORDERED)

        with pytest.raises(BusyError):
            await tx.with_tx(take_head)

        # batch в той же ситуации просто берёт следующие строки.
        rows = await _list(tx, store)
        assert [m.payload for m in rows] == [b'{"id": 11}']
    finally:
        holder_done.set()
        await task


async def test_partitions_are_monthly_and_ensure_is_idempotent(db: DB, pool: Pool) -> None:
    store = Store(db, require_tx=False, clock=Fixed(datetime(2026, 1, 31, 23, 30, tzinfo=UTC)))

    await store.ensure_partitions(1)
    await store.ensure_partitions(1)

    # asyncpg отдаёт "char"-колонку relkind байтами.
    assert await pool.fetchval("SELECT relkind FROM pg_class WHERE relname = 'outbox'") == b"p"
    for name in ("outbox_2026_01", "outbox_2026_02"):
        assert await pool.fetchval("SELECT to_regclass($1)", name) is not None


async def test_retention_drops_old_partitions_but_keeps_unpublished(
    db: DB, pool: Pool, tx: Transactor
) -> None:
    store = _store(db)
    old_empty, sql_empty = partition_sql("outbox", datetime(2020, 1, 1, tzinfo=UTC))
    old_pending, sql_pending = partition_sql("outbox", datetime(2020, 2, 1, tzinfo=UTC))
    await pool.execute(sql_empty)
    await pool.execute(sql_pending)
    await pool.execute(
        "INSERT INTO outbox (subject, payload, created_at) VALUES ($1, $2, $3)",
        "order.created",
        '{"id":12}',
        datetime(2020, 2, 10, tzinfo=UTC),
    )

    with pytest.raises(PendingRowsError, match=old_pending):
        await store.drop_old_partitions(1)

    assert await pool.fetchval("SELECT to_regclass($1)", old_empty) is None
    assert await pool.fetchval("SELECT to_regclass($1)", old_pending) is not None

    await pool.execute("UPDATE outbox SET published_at = NOW() WHERE created_at < NOW()")
    await store.drop_old_partitions(1)
    assert await pool.fetchval("SELECT to_regclass($1)", old_pending) is None


async def test_worker_publishes_batch_in_one_transaction(db: DB, tx: Transactor) -> None:
    store = _store(db)
    recorder = natstest.new()
    outbox = new_outbox(
        _INSTANCE,
        store,
        new_stream_transport(recorder),
        tx,
        Config(batch_size=10),
        Capture(),
    )
    await tx.with_tx(lambda: outbox.publisher().publish("order.created", {"id": 13}))

    await outbox.worker().run()

    assert [m.subject for m in recorder.published()] == ["order.created"]
    assert await db.fetchval("SELECT count(*) FROM outbox WHERE published_at IS NULL") == 0


async def test_concurrent_batch_workers_publish_each_event_once(
    pg: PostgresFixture, db: DB, tx: Transactor
) -> None:
    store = _store(db)
    for i in range(6):
        await _insert(tx, store, "order.created", {"id": 100 + i})

    recorder = natstest.new()
    cfg = Config(batch_size=3)
    workers = [
        new_outbox(_INSTANCE, store, new_stream_transport(recorder), await pg.transactor(), cfg)
        for _ in range(2)
    ]

    await asyncio.gather(*(w.worker().run() for w in workers))
    await asyncio.gather(*(w.worker().run() for w in workers))

    ids = [m.msg_id for m in recorder.published()]
    assert len(ids) == len(set(ids)) == 6
    assert await db.fetchval("SELECT count(*) FROM outbox WHERE published_at IS NULL") == 0


async def test_custom_table_migrations_bootstrap_instance(pg: PostgresFixture, db: DB) -> None:
    source = table_migrations("outbox_orders")
    await asyncio.to_thread(migrate_up_all, pg.dsn, source)
    store = Store(db, "outbox_orders", require_tx=False)
    tx = await pg.transactor()

    async def write() -> None:
        await store.insert("order.created", b'{"id":14}')

    await tx.with_tx(write)
    msgs = await _list(tx, store)

    assert store.table == "outbox_orders"
    assert len(msgs) == 1
    pool = await pg.pool()
    relkind = await pool.fetchval("SELECT relkind FROM pg_class WHERE relname = 'outbox_orders'")
    assert relkind == b"p"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'outbox_orders' AND column_name = 'next_attempt_at'"
        )
        == 1
    )


async def test_store_errors_are_wrapped(pg: PostgresFixture) -> None:
    db = await pg.db()
    store = Store(db, "outbox_missing", require_tx=False)
    tx = await pg.transactor()

    async def take() -> None:
        await store.list_unpublished(1, PublishMode.BATCH)

    with pytest.raises(Exception, match="outboxkit: list"):
        await tx.with_tx(take)


async def _list(
    tx: Transactor, store: Store, mode: PublishMode = PublishMode.BATCH
) -> list[object]:
    rows: list[object] = []

    async def take() -> None:
        rows.extend(await store.list_unpublished(10, mode))

    await tx.with_tx(take)
    return rows


async def test_store_wraps_every_query_error(pg: PostgresFixture) -> None:
    db = await pg.db()
    store = Store(db, "outbox_missing", require_tx=False)
    ref = MessageRef(id=1, created_at=datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(OutboxError, match="outboxkit: insert"):
        await store.insert("order.created", b"{}")
    with pytest.raises(OutboxError, match="outboxkit: mark published"):
        await store.mark_published([ref])
    with pytest.raises(OutboxError, match="outboxkit: record failures"):
        await store.record_failures([Failure(ref=ref, err="x")])
    with pytest.raises(OutboxError, match="outboxkit: revive dead"):
        await store.revive_dead(ReviveFilter())
    with pytest.raises(OutboxError, match="outboxkit: backlog"):
        await store.backlog()
    with pytest.raises(OutboxError, match="ensure partition"):
        await store.ensure_partitions(0)


async def test_retention_picks_up_orphaned_partitions(db: DB, pool: Pool) -> None:
    store = _store(db)
    name, sql = partition_sql("outbox", datetime(2019, 5, 1, tzinfo=UTC))
    await pool.execute(sql)
    # Успевший DETACH с сорвавшимся DROP оставляет сироту — retention обязан её подобрать.
    await pool.execute(f"ALTER TABLE outbox DETACH PARTITION {name}")

    await store.drop_old_partitions(1)

    assert await pool.fetchval("SELECT to_regclass($1)", name) is None


async def test_pgstore_provider_wires_store_and_partitions(pg: PostgresFixture) -> None:
    container = make_async_container(
        pgstore.OutboxPgstoreProvider(), _PgAppProvider(await pg.db(), await pg.transactor())
    )
    try:
        store = await container.get(pgstore.Store)
        repo = await container.get(Repository)

        assert store is repo
        assert store.table == "outbox"
        pool = await pg.pool()
        month = datetime.now(UTC).strftime("outbox_%Y_%m")
        assert await pool.fetchval("SELECT to_regclass($1)", month) is not None
    finally:
        await container.close()


async def test_instance_provider_builds_store_for_its_table(pg: PostgresFixture) -> None:
    await asyncio.to_thread(migrate_up_all, pg.dsn, table_migrations("outbox_leads"))
    recorder = natstest.new()
    container = make_async_container(
        pgstore.OutboxPgstoreFactoryProvider(),
        OutboxInstanceProvider("leads", "outbox_leads"),
        _PgAppProvider(await pg.db(), await pg.transactor(), recorder),
    )
    try:
        outbox = await container.get(Outbox, component="leads")
        publisher = await container.get(Publisher, component="leads")

        async def write() -> None:
            await publisher.publish("lead.created", {"id": 15})

        await (await pg.transactor()).with_tx(write)
        await outbox.worker().run()

        assert [m.msg_id for m in recorder.published()] == ["leads:1"]
        assert [job.name for job in outbox.jobs()] == [
            "outboxkit.publisher.leads",
            "outboxkit.partition-keeper.outbox_leads",
        ]
    finally:
        await container.close()
