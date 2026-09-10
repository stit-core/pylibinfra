# pgkit — рецепты

Сигнатуры даны как в коде. Идиоматичный путь — через `PgProvider`/
`PgMigrateProvider` (dishka); прямые конструкторы (`new_pool`, `DB`,
`PoolTransactor`, `ReadWriteDB`) допустимы, но менее однородны с остальной
библиотекой.

## Подключение через dishka

`PgProvider` provide'ит `Pool`, `DB`, `Transactor` и `ReadWriteDB`; пулы
закрываются на выходе из контейнера. Config'и регистрирует caller.

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.pgkit import DB, PgProvider, PoolConfig, new_pool_config


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def pool_config(self) -> PoolConfig:
        return new_pool_config()


container = make_async_container(PgProvider(), AppProvider())
db = await container.get(DB)
```

Реплика опциональна: `PgProvider` даёт дефолт «реплики нет», а сервис с репликой
перекрывает его фабрикой типа-обёртки — свой Provider регистрируется ПОСЛЕ:

```python
from pylibinfra.pgkit import OptionalReplicaConfig, new_replica_config


class ReplicaProvider(Provider):
    scope = Scope.APP

    @provide
    def replica_config(self) -> OptionalReplicaConfig:
        return new_replica_config()  # PG_REPLICA_DSN


container = make_async_container(PgProvider(), AppProvider(), ReplicaProvider())
```

`pg_pool_*` идут в глобальный Meter; без `MeterProvider` (потребитель не
подключил OTel SDK) инструменты — no-op, граф при этом поднимается.

## Базовые запросы

```python
class Repo:
    def __init__(self, db: DB) -> None:
        self._db = db

    async def count(self) -> int:
        return int(await self._db.fetchval("SELECT count(*) FROM users"))
```

Каждый вызов `db.execute/fetch/fetchrow/fetchval` идёт через `conn(pool)`:
внутри `Transactor.with_tx` он автоматически использует транзакцию из
contextvar, вне её — пул. Репозиторий пишется одинаково для обоих случаев.

## Транзакции через `Transactor`

```python
class Service:
    def __init__(self, db: DB, transactor: Transactor) -> None:
        self._db = db
        self._tx = transactor

    async def transfer(self, source: int, target: int, amount: int) -> None:
        async def body() -> None:
            await self._db.execute(
                "UPDATE accounts SET balance = balance - $1 WHERE id = $2", amount, source
            )
            await self._db.execute(
                "UPDATE accounts SET balance = balance + $1 WHERE id = $2", amount, target
            )

        await self._tx.with_tx(body)
```

- Исключение из `fn` → `ROLLBACK`; исключение пробрасывается как есть (тип и
  `__cause__` сохраняются). Ошибка отката добавляется к нему примечанием.
  Ошибки `BEGIN`/`COMMIT` — `pgkit.TransactionError`.
- Отмена таски внутри `fn` → `ROLLBACK`, `asyncio.CancelledError` летит дальше.
- Вложенный вызов (когда транзакция уже открыта) участвует во внешней
  транзакции, не открывая новую и не создавая savepoint; его `options`
  игнорируются.
- `in_tx()` — проверить, выполняется ли код внутри транзакции (нужно коду,
  обязанному быть атомарным с бизнес-записью, например outbox-insert). В тестах
  тот же гейт открывает `pgkit.with_test_tx()` — маркер без Postgres, который
  кладут фейки `testkit/pgtest`.

Уровень изоляции по умолчанию — серверный (`read committed`). Другой уровень или
read-only транзакция — через `options`:

```python
from pylibinfra.pgkit import TxOptions

await transactor.with_tx(body, options=TxOptions(isolation="serializable"))
await transactor.with_tx(report, options=TxOptions(read_only=True))
```

Для конфликтов сериализации и дедлоков повторяй операцию по `is_retryable`.

## Обработка pg-ошибок

Предикаты распознают и обёрнутую ошибку (идут по цепочке `__cause__`):

```python
from pylibinfra.pgkit import is_foreign_key_violation, is_unique_violation

try:
    await db.execute("INSERT INTO users (email) VALUES ($1)", email)
except Exception as exc:
    if is_unique_violation(exc):
        raise EmailTakenError from exc
    if is_foreign_key_violation(exc):
        raise BadReferenceError from exc
    raise
```

```python
row = await db.fetchrow("SELECT id FROM users WHERE email = $1", email)
if row is None:
    raise NotFoundError
```

Повтор транзиентных конфликтов:

```python
from pylibinfra.pgkit import is_retryable

for _ in range(max_retries):
    try:
        await transactor.with_tx(do_work)
        break
    except Exception as exc:
        if not is_retryable(exc):
            raise
        await backoff()
```

«Кто первый, тот и работает» через `FOR UPDATE NOWAIT`:

```python
from pylibinfra.pgkit import is_lock_not_available

try:
    await db.execute("SELECT id FROM jobs WHERE id = $1 FOR UPDATE NOWAIT", job_id)
except Exception as exc:
    if is_lock_not_available(exc):
        return  # строку держит другой инстанс — пропускаем
    raise
```

`is_query_canceled(exc)` отличает срабатывание `statement_timeout` от прочих
сбоев запроса.

## Миграции

### На старте приложения (рекомендуемо)

`PgMigrateProvider` под advisory-lock применяет источники китов из
`pgkit.MigrationSources`, затем каталог приложения из `PG_MIGRATIONS_PATH`
(default `/migrations`; пустая строка — своих миграций нет). DSN берётся из
`PoolConfig`.

```python
from pylibinfra.auditkit.pgstore import MIGRATIONS as AUDIT_MIGRATIONS
from pylibinfra.pgkit import MigrationSources, PgMigrateProvider, PgProvider


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def migration_sources(self) -> MigrationSources:
        return (AUDIT_MIGRATIONS,)


container = make_async_container(PgProvider(), PgMigrateProvider(), AppProvider())
await container.get(Migrated)  # применить схему до приёма трафика
```

У каждого источника своя таблица версий: `schema_migrations_app` для приложения,
`schema_migrations_<kit>` для китов. Advisory-lock гарантирует, что при
одновременном старте нескольких инстансов миграции применяет ровно один —
остальные ждут его до `PG_MIGRATE_TIMEOUT` (default 120 s) и находят схему уже
применённой; не дождавшийся инстанс падает на старте и уходит в рестарт.

Ресурсу, которому нужна применённая схема, объяви зависимость от
`pgkit.Migrated` — тогда порядок не зависит от порядка регистрации провайдеров:

```python
@provide
async def partitions(self, db: DB, migrated: Migrated) -> Partitions:
    return await create_partitions(db)
```

Свой источник (например, миграции внутреннего пакета приложения):

```python
Source(name="billing", path=str(files("app.billing") / "migrations"))
```

### Программно

```python
src = Source(name=AppSourceName, path="./migrations")

migrate_up(dsn, src)
version = migrate_version(dsn, src)  # NNNN последней применённой; 0 — ни одной
migrate_down(dsn, src)  # один шаг назад

migrate_up_all(dsn, AUDIT_MIGRATIONS, OUTBOX_MIGRATIONS, src, timeout=120.0)
```

Все четыре функции синхронные (yoyo ходит в базу через psycopg2): из async-кода
зови их через `asyncio.to_thread`. `migrate_up` молча игнорирует «нечего
применять». Дедлайн ожидания advisory-lock'а у `migrate_up_all` задаёт
`timeout`; уже идущий SQL-оператор он не прерывает.

### Файлы миграций

`NNNN_name.sql` и опциональный `NNNN_name.rollback.sql` в одном каталоге.
Многооператорные файлы допустимы. Оператору, который нельзя выполнять в
транзакции (`CREATE INDEX CONCURRENTLY`), нужна директива первой строкой:

```sql
-- transactional: false
CREATE INDEX CONCURRENTLY IF NOT EXISTS audit_log_at_idx ON audit_log (at DESC);
```

## Read-replica

```python
class Repo:
    def __init__(self, rw: ReadWriteDB) -> None:
        self._rw = rw

    async def list_public(self) -> list[Record]:
        return await self._rw.read().fetch("SELECT * FROM items WHERE public")

    async def create(self, item_id: int) -> None:
        await self._rw.write().execute("INSERT INTO items (id) VALUES ($1)", item_id)
```

Роутинг **explicit** — kit не парсит SQL. Если `PG_REPLICA_DSN` пуст, `read()`
тоже идёт на мастер, поэтому read/write-split-код работает и без реплики. Не
читай через `read()` то, что только что записал через `write()`
(read-after-write не гарантируется из-за лага репликации) — такие чтения веди
через `write()`.

## Health-пробы

```python
@provide
def probes(self, db: DB, rw: ReadWriteDB) -> healthkit.Probes:
    return (probe(db), replica_probe(rw))
```

`probe(db)` (имя `pgkit`) даёт `/ready` → 503, если `SELECT 1` не проходит;
`replica_probe(rw)` (имя `pgkit/replica`) без `PG_REPLICA_DSN` проходит без
запроса.

## UUID и jsonb

```python
from pylibinfra.pgkit import parse_uuid, unmarshal_jsonb

user_id = parse_uuid(raw)  # InvalidUUIDError на пустой/битой строке
payload = unmarshal_jsonb(row["metadata"])  # None на NULL и пустой строке
```

asyncpg сам кодирует `uuid.UUID` в колонку `uuid`. Колонки `json`/`jsonb`
приходят текстом: кодек кит намеренно не ставит (см.
[architecture.md § Тип jsonb](architecture.md)).

## DSN в логах

```python
from pylibinfra.pgkit import redact_dsn

log.info("connecting", dsn=redact_dsn(cfg.dsn.get_secret_value()))
# postgresql://app:xxxxx@db:5432/app
```

Свои ошибки кит редактирует сам, эта функция — для строк подключения, которые
потребитель печатает сам (стартовый лог, диагностика).
