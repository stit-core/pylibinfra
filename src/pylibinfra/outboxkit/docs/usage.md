# Использование outboxkit

Сигнатуры приведены как в коде; импорты опущены ради краткости.

## Сигнатуры API

```python
class Publisher(Protocol):
    async def publish(self, subject: str, payload: Any) -> None


class Repository(Protocol):
    async def insert(self, subject: str, payload: bytes) -> None
    async def list_unpublished(self, limit: int, mode: PublishMode) -> list[Message]
    async def mark_published(self, refs: Sequence[MessageRef]) -> None
    async def record_failures(self, failures: Sequence[Failure]) -> None


class Transport(Protocol):
    async def publish(self, msg: OutMsg) -> None
    async def publish_batch(self, msgs: Sequence[OutMsg]) -> list[Exception | None]


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    created_at: datetime      # ключ партиционирования
    subject: str
    payload: bytes
    attempts: int = 0

    def ref(self) -> MessageRef


@dataclass(frozen=True, slots=True)
class Failure:
    ref: MessageRef
    err: str
    retry_after: float = 0.0  # секунды до следующей попытки
    dead: bool = False


def new_outbox(name, repo, tr, tx, cfg, log=None) -> Outbox
def new_stream_transport(sb: natskit.StreamBus, namespace: str = "") -> Transport

outbox.publisher() -> Publisher
outbox.bus() -> Bus
outbox.worker() -> Worker
outbox.job() -> timerkit.Job                # drain инстанса
outbox.jobs() -> list[timerkit.Job]         # + партиции и retention от store'а
outbox.register_backlog_gauges() -> BacklogRegistration | None
await outbox.revive_dead(ReviveFilter(...)) -> int
```

## Публикация в транзакции

`Publisher.publish` вызывается внутри той же транзакции, что и доменная запись —
в этом весь смысл паттерна:

```python
async def create_order(order: Order) -> None:
    async def unit() -> None:
        await orders.insert(order)
        await publisher.publish("order.created", {"id": order.id})

    await transactor.with_tx(unit)
```

`payload` — любое значение, сериализуемое `json.dumps` (или готовые `bytes`);
сериализует его `Bus`. Вне транзакции при `OUTBOX_REQUIRE_TX=true` (default)
`publish` поднимает `outboxkit.NoTxError`.

## Single-instance: dishka

`OutboxPgstoreProvider` даёт `Store` как `outboxkit.Repository` и создаёт
партиции после миграций; `OutboxProvider` — `Bus`/`Publisher`/`Worker`/`Outbox` и
backlog-gauge'и. Job'ы консьюмер кладёт в кортеж `timerkit.Jobs` (полный пример —
[README кита](../README.md)):

```python
class AppConfigs(Provider):
    scope = Scope.APP

    @provide
    def migrations(self) -> pgkit.MigrationSources:
        return (pgstore.MIGRATIONS,)

    @provide
    def jobs(self, outbox: outboxkit.Outbox) -> timerkit.Jobs:
        return tuple(outbox.jobs())  # publisher + partition-keeper + retention
```

## Multi-instance: инстанс на таблицу

Несколько независимых outbox'ов (таблица на домен) в одном приложении.
`OutboxPgstoreFactoryProvider` даёт фабрику store'ов, `OutboxInstanceProvider`
собирает инстанс целиком: store на свою таблицу, транспорт с namespace'ным
`Nats-Msg-Id`, drain- и maintenance-job'ы, backlog-gauge'и. Каждый инстанс живёт
в своём dishka-компоненте (по умолчанию — его имя).

```python
class AppConfigs(Provider):
    scope = Scope.APP

    @provide
    def migrations(self) -> pgkit.MigrationSources:
        return (
            pgstore.table_migrations("outbox_orders"),
            pgstore.table_migrations("outbox_leads"),
        )

    @provide
    def jobs(
        self,
        orders: Annotated[outboxkit.Outbox, FromComponent("orders")],
        leads: Annotated[outboxkit.Outbox, FromComponent("leads")],
    ) -> timerkit.Jobs:
        return (*orders.jobs(), *leads.jobs())


container = make_async_container(
    pgstore.OutboxPgstoreFactoryProvider(),  # RepositoryFactory
    outboxkit.OutboxInstanceProvider("orders", "outbox_orders"),
    outboxkit.OutboxInstanceProvider("leads", "outbox_leads", cfg=ordered_cfg),
    AppConfigs(),
    # + logkit/pgkit/natskit/timerkit-провайдеры
)

orders_publisher = await container.get(outboxkit.Publisher, component="orders")
```

`OutboxPgstoreProvider` (таблица `outbox`) и `OutboxPgstoreFactoryProvider`
вместе не подключаются: первый держит одну таблицу, вторая — таблицы инстансов.

## Multi-instance вручную

Когда сборка не через DI:

```python
store = pgstore.new_store(db, pgstore.new_config(), "outbox_orders")
await store.ensure_partitions(1)

orders = outboxkit.new_outbox(
    "orders", store, outboxkit.new_stream_transport(stream_bus, "orders"), transactor, cfg
)

publisher = orders.publisher()  # туда, где нужна доменная публикация
jobs = orders.jobs()  # drain + партиции + retention → timerkit.Jobs
registration = orders.register_backlog_gauges()  # unregister() на shutdown
```

Схему кастомной таблицы накатывает консьюмер — `pgstore.table_migrations(table)`
в `pgkit.MigrationSources` либо `pgstore.schema_sql(table)` в собственной
миграции приложения.

## Оживление dead-строк

Строки, исчерпавшие `OUTBOX_MAX_ATTEMPTS`, остаются в таблице с `dead_at`,
`attempts` и `last_error`. Вернуть их в очередь (после починки причины):

```python
revived = await outbox.revive_dead(
    outboxkit.ReviveFilter(
        subject="order.created",  # пусто — любой subject
        since=datetime.now(UTC) - timedelta(days=7),  # None — без границы
    )
)
```

Фильтр отбирает dead-строки по `ids`, `subject` и `since` (нижняя граница
`created_at`); поля соединяются по И, пустой фильтр берёт все dead-строки
таблицы. Сбрасываются `dead_at`, `attempts`, `last_error` и пауза backoff;
результат — число оживлённых строк, оно же уходит в `outbox_revived_total`.
Хранилище без поддержки — `outboxkit.NotSupportedError`.

## Ручной drain и прямые методы store'а

Обычно тик гоняет `timerkit` через `Outbox.job()`. Прямые методы доступны, если
нужен кастомный drain:

```python
await outbox.worker().run()  # один тик: list → publish → mark, в одной транзакции


async def unit() -> None:
    msgs = await store.list_unpublished(100, outboxkit.PublishMode.BATCH)
    ...
    await store.mark_published([m.ref() for m in msgs])


await transactor.with_tx(unit)  # list запирает строки до конца транзакции
```

`mark_published` и `record_failures` принимают `MessageRef` (его отдаёт
`Message.ref()`): `created_at` — ключ партиционирования, с ним UPDATE идёт по
партициям батча, а не по всем.

## BacklogReporter (lag / pending)

`pgstore.Store` реализует `outboxkit.BacklogReporter`. Провайдеры подключают
gauge'и автоматически; вручную:

```python
registration = outbox.register_backlog_gauges()
if registration is not None:
    ...  # на shutdown: registration.unregister()

snapshot = await store.backlog()  # [SubjectBacklog(subject, oldest, pending), …]
```

Это включает `outbox_lag_seconds{instance,subject}` и
`outbox_pending_count{instance,subject}` (см. [observability.md](observability.md)).
