# auditkit

Append-only audit-log для сервисов stit: «кто / когда / что сделал над каким
ресурсом». Кит фиксирует действия (`actor_id`, `action`, `resource`,
`resource_id`, `metadata`, `ip`, `request_id`, `at`) в отдельную таблицу, чтобы
compliance- и forensics-запросы не приходилось склеивать из россыпи прикладных
логов. Core-абстракция (`Entry`, `Logger`) отделена от хранилища; единственная
реализация в библиотеке — `pgstore` поверх `pgkit`.

Место в стеке: бизнес-сервис зовёт `auditkit.Logger.log` в точках значимого
изменения состояния (создание/удаление сущности, смена прав, вход в систему).
Запись синхронная: `log` пишет в транзакцию из contextvar
(`pgkit.Transactor.with_tx`), если она открыта, иначе автономно, отдельным
соединением пула в autocommit.

## Импорт

```python
from pylibinfra.auditkit import Entry, Logger
from pylibinfra.auditkit.pgstore import MIGRATIONS, AuditPgstoreProvider, Store
```

Версия и установка — см. [корневой README](../../../README.md).

## Быстрый старт

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.auditkit import Entry, Logger
from pylibinfra.auditkit.pgstore import MIGRATIONS, AuditPgstoreProvider
from pylibinfra.pgkit import (
    DB,
    Migrated,
    MigrateRunnerConfig,
    MigrationSources,
    PgMigrateProvider,
    PgProvider,
    PoolConfig,
    Transactor,
    new_migrate_runner_config,
    new_pool_config,
)


class UserService:
    def __init__(self, db: DB, transactor: Transactor, audit: Logger) -> None:
        self._db = db
        self._tx = transactor
        self._audit = audit

    async def create(self, actor_id: str, user_id: str) -> None:
        # Запись аудита — в той же транзакции, что и доменное изменение.
        async def body() -> None:
            await self._db.execute("INSERT INTO users (id) VALUES ($1)", user_id)
            await self._audit.log(
                Entry(
                    action="user.create",
                    resource="user",
                    actor_id=actor_id,
                    resource_id=user_id,
                    metadata={"source": "admin-ui"},
                    ip="203.0.113.7",
                    request_id="req-abc",
                )
            )

        await self._tx.with_tx(body)


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def pool_config(self) -> PoolConfig:
        return new_pool_config()

    @provide
    def migrate_config(self) -> MigrateRunnerConfig:
        return new_migrate_runner_config()

    @provide
    def migration_sources(self) -> MigrationSources:
        return (MIGRATIONS,)  # создаёт таблицу audit_log

    @provide
    def user_service(self, db: DB, transactor: Transactor, audit: Logger) -> UserService:
        return UserService(db, transactor, audit)


container = make_async_container(
    PgProvider(), PgMigrateProvider(), AuditPgstoreProvider(), AppProvider()
)
await container.get(Migrated)
users = await container.get(UserService)
```

Без DI:

```python
store = Store(db)  # db: pgkit.DB
await store.log(Entry(action="session.login", resource="session"))
```

### Проводка миграций

Таблицу `audit_log` создают миграции источника `pgstore.MIGRATIONS`
(`pgkit.Source(name="auditkit")`, таблица версий `schema_migrations_auditkit`).
Консьюмер кладёт его в `pgkit.MigrationSources`, и `PgMigrateProvider` применяет
его на старте вместе с миграциями приложения. Без DI:

```python
migrate_up_all(dsn, MIGRATIONS, Source(name=AppSourceName, path="./migrations"))
```

## Конфигурация

Своей `Config` и ENV-переменных у кита нет. Единственный внешний параметр —
`pgkit.DB`, который `Store` получает в конструкторе (через провайдер или
напрямую). Подключение к Postgres настраивается в `pgkit` (`PG_DSN`).

## Observability

`Store` эмитит через глобальные OTel-провайдеры (scope `pylibinfra.auditkit`);
без зарегистрированного `MeterProvider` инструменты — no-op.

| Метрика | Тип | Labels | Смысл |
|---|---|---|---|
| `audit_log_writes_total` | counter | `result=ok\|error\|invalid` | Исход `log`: `ok` — строка записана, `error` — INSERT не прошёл, `invalid` — запись отвергнута до похода в базу (`Entry.validate` или несериализуемая `metadata`) |
| `audit_log_write_duration_seconds` | histogram (`s`) | `result=ok\|error` | Длительность INSERT'а; границы — общие `otelkit.DURATION_BUCKETS` |

Потребители обычно логируют ошибку `log` и продолжают доменную операцию, поэтому
`rate(audit_log_writes_total{result!="ok"})` — единственный алертируемый сигнал
потери записи аудита.

Собственных спанов кит не заводит: `Store.log` выполняет INSERT через `pgkit.DB`,
поэтому каждая запись попадает в per-query span otel-asyncpg и в метрики пула
`pg_pool_*`. Своего логгера у кита нет — ошибки поднимаются исключениями.

## API

### `Entry`

Одна запись аудита (frozen dataclass).

| Поле | Тип | Ограничение | Назначение |
|---|---|---|---|
| `action` | `str` | обязательно, ≤ `MAX_ACTION_LEN` (255) | что сделано, напр. `user.create` (`NOT NULL`) |
| `resource` | `str` | обязательно, ≤ `MAX_RESOURCE_LEN` (255) | над каким типом ресурса, напр. `user` (`NOT NULL`) |
| `actor_id` | `str` | ≤ `MAX_ACTOR_ID_LEN` (255) | кто совершил действие; пусто → `NULL` |
| `resource_id` | `str` | ≤ `MAX_RESOURCE_ID_LEN` (255) | id конкретного ресурса; пусто → `NULL` |
| `metadata` | `Mapping[str, Any]` | длина не ограничена китом | произвольный контекст; сериализуется в `JSONB`; пусто → `NULL` |
| `at` | `datetime \| None` | timezone-aware | момент события; `None` → `NOW()` базы, naive `datetime` отвергается |
| `ip` | `str` | длина не ограничена китом | IP инициатора; пусто → `NULL` |
| `request_id` | `str` | длина не ограничена китом | корреляция с HTTP/RPC-запросом; пусто → `NULL` |

Предельные длины — ровно у полей, попадающих в btree-индексы `audit_log`:
значение длиннее не помещается в индексную запись Postgres, и INSERT падает уже
в базе.

### `Entry.validate` и `InvalidEntryError`

```python
def validate(self) -> None  # InvalidEntryError
```

Требует непустых `action` и `resource`, соблюдения предельных длин и
timezone-aware `at`. `Store.log` зовёт её до похода в базу: пустая запись в
audit-log хуже ошибки — её никто не заметит. Так же ведёт себя фейк
`testkit.audittest.Recorder`. `InvalidEntryError` — подкласс `AuditError` (база
ошибок кита) и `ValueError`.

### `Logger`

```python
class Logger(Protocol):
    async def log(self, entry: Entry) -> None: ...
```

Порт записи аудита. Бизнес-код зависит от него, а не от конкретного хранилища;
свой адаптер для другого бэкенда реализуется этим же протоколом.

### `pgstore.Store`

`Store(db: pgkit.DB)` — реализация `Logger` поверх Postgres. `log` делает один
INSERT в `audit_log`. Невалидная `Entry` (включая naive `at`) и несериализуемая
`metadata` отвергаются до запроса; пустые строковые поля и пустая `metadata`
пишутся как `NULL`; `at is None` подставляется как `NOW()` базы (`COALESCE`).
Ошибка базы — `AuditError` с префиксом `auditkit:`.

### `pgstore.AuditPgstoreProvider`

dishka-провайдер: отдаёт `Store` под портом `auditkit.Logger`. `pgkit.DB`
регистрирует caller (`PgProvider`), `MIGRATIONS` он же кладёт в
`pgkit.MigrationSources`.

### `pgstore.MIGRATIONS`

`pgkit.Source(name="auditkit")` с SQL-миграциями кита. См. «Проводка миграций».

### Схема `audit_log`

```sql
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
CREATE INDEX audit_log_actor_idx    ON audit_log (actor_id, at DESC);
CREATE INDEX audit_log_resource_idx ON audit_log (resource, resource_id, at DESC);
-- триггер audit_log_block_mutate роняет UPDATE и DELETE
-- отдельная миграция 0002
CREATE INDEX CONCURRENTLY IF NOT EXISTS audit_log_at_idx ON audit_log (at DESC);
```

Индексы рассчитаны на типовые запросы: «история действий актора», «история по
конкретному ресурсу» и «всё за период» (retention, выгрузки), все — в обратном
хронологическом порядке.

`audit_log_at_idx` приезжает миграцией `0002_audit_log_at_idx` и строится
`CONCURRENTLY`: на уже наполненной `audit_log` обычный `CREATE INDEX` держал бы
`SHARE`-блокировку и останавливал запись в таблицу на всё время построения.
Условия, при которых это работает и о которых нужно знать:

- **Файл миграции несёт директиву `-- transactional: false`.** `CONCURRENTLY`
  внутри транзакционного блока Postgres запрещён, а yoyo по умолчанию оборачивает
  миграцию в транзакцию.
- **Старт всё равно ждёт построения.** `PgMigrateProvider` применяет миграции до
  готовности контейнера, а ограничить время нечем: `PG_MIGRATE_TIMEOUT` доходит
  только до ожидания advisory-lock'а, `PG_STATEMENT_TIMEOUT` — параметр пула и на
  соединение миграций не распространяется. На большой таблице стройте индекс
  заранее вручную (`CREATE INDEX CONCURRENTLY IF NOT EXISTS audit_log_at_idx …`)
  — миграция увидит его и станет no-op.
- **Провал `CONCURRENTLY` оставляет невалидный индекс.** Его видно как
  `indisvalid = false` в `pg_index`; повторный прогон миграции из-за
  `IF NOT EXISTS` его не чинит — снимите (`DROP INDEX CONCURRENTLY
  audit_log_at_idx`) и накатите миграцию заново.

## Тестирование

Интеграционный тест поднимает реальный Postgres через
[`testkit.start_postgres`](../testkit/) с миграцией кита:

```python
async with start_postgres(migrations=[MIGRATIONS]) as pg:
    store = Store(await pg.db())
    await store.log(Entry(action="user.create", resource="user"))
```

Покрытие: полная запись и `NULL`-поля, `at` из часов базы, участие в транзакции
(откат оставляет 0 строк, коммит — 1, вне транзакции строка пишется сразу),
ошибка INSERT с префиксом `auditkit:`, отказ на невалидной `Entry` (пустые поля,
naive `at`), append-only-триггер, сборка провайдера, метрики, индекс миграции
`0002`, идемпотентность повторного прогона источника и откат обеих миграций.

Для unit-тестов бизнес-кода подменяйте `auditkit.Logger` фейком
[`testkit/audittest`](../testkit/audittest/): `Recorder` валидирует `Entry` так
же, как `pgstore`, хранит копию `metadata` и отвечает на `entries/actions/has/
last/by_resource/reset`.

## Инварианты и подводные камни

- **Core + pgstore split.** Доменный код импортирует только `auditkit` (`Entry`,
  `Logger`); зависимость на Postgres локализована в `pgstore`. Это держит
  бизнес-слой свободным от инфраструктуры и позволяет завести другой бэкенд, не
  трогая вызывающий код.
- **Append-only на уровне БД.** Кит только пишет (INSERT), а триггер
  `audit_log_block_mutate` роняет `UPDATE`/`DELETE`: аудит нельзя подправить или
  вычистить задним числом даже скомпрометированной app-ролью. Ротация старых
  записей поэтому идёт партиционированием и `TRUNCATE`/`DROP` партиции (row-level
  триггер на них не срабатывает), а не построчным `DELETE`.
- **Транзакция берётся из contextvar.** `log` пишет в транзакцию, если код идёт
  внутри `pgkit.Transactor.with_tx`: тогда запись атомарна с доменной операцией и
  ошибка `log`, выпущенная наружу, откатывает обе (fail-closed). Вне `with_tx`
  `log` берёт отдельное соединение пула и коммитит сам (autocommit): доменная
  операция уже зафиксирована, и потеря записи аудита остаётся на усмотрение
  приложения (fail-open, сигнал — `audit_log_writes_total{result!="ok"}`).
  Атомарности «сама по себе» синхронность записи не даёт.
- **Обязательные поля.** `action` и `resource` непусты и укладываются в предельные
  длины, иначе `log` поднимает `InvalidEntryError` и в базу не идёт.
- **Что класть в audit-log.** Значимые изменения состояния и действия,
  релевантные для compliance/forensics, — не трассировку и не debug-логи. Для них
  есть `logkit` и спаны. `metadata` — для структурированного контекста события,
  не для дампа payload'а.
- **Время.** `at is None` заменяется на `NOW()` базы — единые часы для всех
  записей независимо от часов инстансов сервиса; колонка `TIMESTAMPTZ`. Для
  детерминированных тестов передавайте `at` явно — обязательно с таймзоной
  (`datetime.now(UTC)`): naive-значение драйвер записал бы по часовому поясу
  процесса, и момент события сдвинулся бы на локальное смещение молча, поэтому
  `Entry.validate` его отвергает.
- **PII.** `actor_id`, `ip`, `metadata` могут содержать персональные данные.
  Redact-фильтры `logkit` сюда не применяются — за минимизацией PII в записях
  следит вызывающий код.
