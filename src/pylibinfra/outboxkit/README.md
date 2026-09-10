# outboxkit

Transactional outbox для сервисов stit. `Bus` вставляет событие в outbox-таблицу
в той же транзакции, что и доменный state; `Worker` тиком — одной транзакцией
list → publish → mark — вычитывает неопубликованные строки и публикует их в
JetStream через `natskit.StreamBus`, помечая published только подтверждённые
`PubAck`'ом. Гарантия доставки — at-least-once; id строки уходит как
`Nats-Msg-Id`, поэтому переотправка на ретрае дедуплицируется стримом.

Это решает классическую проблему dual-write: «закоммитили в БД, но упали до
публикации в брокер» (или наоборот). Outbox делает запись факта и его публикацию
одним атомарным коммитом.

Хранилище — Postgres **14 или новее**: retention отцепляет партиции
`ALTER TABLE … DETACH PARTITION … CONCURRENTLY`.

## Импорт

```python
from pylibinfra import outboxkit
from pylibinfra.outboxkit import pgstore
```

Версионирование и установка — в [корневом README](../../../README.md)
(§ Версионирование, § Стабильность API).

## Быстрый старт

Single-instance путь (одна таблица `outbox`). Нужны `logkit`, `pgkit` (с
`PgMigrateProvider` — он применяет миграции кита), `natskit` (с
`NATS_JETSTREAM_ENABLED=true` и стримом, покрывающим subject'ы outbox'а) и
`timerkit` (он гоняет тик воркера). Конфиги регистрирует приложение.

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import clockkit, logkit, natskit, outboxkit, pgkit, timerkit
from pylibinfra.outboxkit import pgstore


class AppConfigs(Provider):
    scope = Scope.APP

    @provide
    def log(self) -> logkit.Config:
        return logkit.new_config()

    @provide
    def pool(self) -> pgkit.PoolConfig:
        return pgkit.new_pool_config()

    @provide
    def migrate(self) -> pgkit.MigrateRunnerConfig:
        return pgkit.new_migrate_runner_config()

    @provide
    def migrations(self) -> pgkit.MigrationSources:
        return (pgstore.MIGRATIONS,)  # миграции кита в общий прогон

    @provide
    def nats(self) -> natskit.Config:
        return natskit.new_config()

    @provide
    def outbox(self) -> outboxkit.Config:
        return outboxkit.new_config()  # OUTBOX_*

    @provide
    def outbox_store(self) -> pgstore.Config:
        return pgstore.new_config()  # OUTBOX_RETENTION_MONTHS, OUTBOX_REQUIRE_TX

    @provide
    def jobs(self, outbox: outboxkit.Outbox) -> timerkit.Jobs:
        return tuple(outbox.jobs())  # drain + партиции + retention


async def run() -> None:
    container = make_async_container(
        logkit.LogkitProvider(),
        pgkit.PgProvider(),
        pgkit.PgMigrateProvider(),
        natskit.NatsProvider(),
        pgstore.OutboxPgstoreProvider(),  # Store как outboxkit.Repository + партиции
        outboxkit.OutboxProvider(),  # Bus, Publisher, Worker, job'ы
        clockkit.ClockProvider(),  # часы расписания timerkit
        timerkit.TimerkitProvider(),  # гоняет Outbox.job()
        AppConfigs(),
    )
    publisher = await container.get(outboxkit.Publisher)
    transactor = await container.get(pgkit.Transactor)

    async def create_order() -> None:
        # публикация — в той же транзакции, что и доменная запись
        await publisher.publish("order.created", {"id": 42})

    await transactor.with_tx(create_order)
    await container.close()
```

`OUTBOX_REQUIRE_TX=true` (default) отвергает `publish` вне транзакции
(`outboxkit.NoTxError`) — иначе теряется смысл паттерна.

## Документация

- [docs/architecture.md](docs/architecture.md) — как устроено: транзакционный тик и гарантии при нескольких репликах, порядок публикации, backoff и poison→dead, lifecycle остановки, партиции и retention, single- vs multi-instance, namespaced `Nats-Msg-Id`, решения (WHY).
- [docs/configuration.md](docs/configuration.md) — полный справочник ENV, тюнинг publish-mode / max-attempts / backoff / tick-timeout / retention, миграции и схема таблицы.
- [docs/observability.md](docs/observability.md) — метрики (label `instance`), спаны, `outbox_lag_seconds`/`outbox_pending_count` через `BacklogReporter`.
- [docs/usage.md](docs/usage.md) — рецепты: single-instance, multi-instance инстансы, оживление dead-строк, ручной drain, `BacklogReporter`.
- [docs/testing.md](docs/testing.md) — фейки `Repository`/`Transport`, `testkit.outboxtest`, интеграционные тесты pgstore.
