# pgkit — архитектура

Фасад поверх [asyncpg](https://github.com/MagicStack/asyncpg): пул соединений,
`Transactor` с проброской транзакции через `contextvars`, migrate-runner поверх
[yoyo](https://ollycope.com/software/yoyo/), маппинг pg-ошибок в булевы
предикаты, health-пробы и опциональная read-replica. Пул инструментируется
метриками `pg_pool_*` и per-query спанами `opentelemetry-instrumentation-asyncpg`.

Kit даёт сервису соединение, надёжность пула и атомарность записи; SQL, схема и
доменные репозитории — на стороне потребителя.

## Слои

```
PoolConfig ──► new_pool ──► asyncpg.Pool ──┬─► DB (фасад execute/fetch/fetchrow/fetchval)
                                           ├─► PoolTransactor (Transactor)
                                           └─► instrument_pool (pg_pool_*{role})

ReplicaConfig ──► ReadWriteDB { master, replica }
```

`asyncpg.Pool` — единственный «тяжёлый» ресурс; `DB`, `PoolTransactor` и
`ReadWriteDB` — тонкие обёртки поверх него.

## Пул соединений (`pool.py`)

`new_pool(cfg)` кладёт размеры и таймауты в `asyncpg.create_pool` и возвращает
пул, проверенный `SELECT 1`. `PG_CONNECT_TIMEOUT` ограничивает установку каждого
физического соединения (`timeout=`) и стартовый ping. При `PG_OTEL_TRACE=true`
(default) один раз на процесс включается `AsyncPGInstrumentor` — он даёт спаны
запросов.

Нулевое значение размера или таймаута означает **дефолт asyncpg**: оно не
переносится в `create_pool`. WHY: перенесённые нули ломают пул изнутри
(`max_size=0` отвергается, `max_inactive_connection_lifetime=0` снимает
ротацию). Конфиг с одним `PG_DSN` обязан быть рабочим пулом, а не миной.
Комбинация, которую asyncpg принять не может (`PG_MIN_CONNS` больше дефолтного
`max_size`, когда `PG_MAX_CONNS=0`), поднимает `pgkit.ConfigError` с текстом
asyncpg — оператор видит, какую переменную править.

Сессионные таймауты (`statement_timeout`, `idle_in_transaction_session_timeout`,
`lock_timeout`) переводятся в миллисекунды и кладутся в `server_settings` — они
уезжают в StartupMessage при установке соединения.

WHY `server_settings`, а не `SET` в `init`-хуке: `SET` стоит round-trip'ов на
каждом новом физическом соединении (реконнекты, ротация по
`PG_MAX_CONN_IDLE_TIME`), а StartupMessage — ноль. WHY не параметры DSN:
таймауты остаются частью ENV-контракта, а не «магией» внутри строки подключения.
За PgBouncer в `transaction`-режиме сессионные параметры не гарантируются.

Чего у asyncpg нет: плановой ротации по возрасту соединения и периодического
health-check'а пула. Переменных под них у кита поэтому нет; ротацию
простаивающих соединений задаёт `PG_MAX_CONN_IDLE_TIME`, а обрыв мёртвого
соединения ловит сам запрос.

DSN в текст ошибки не попадает: `new_pool` и migrate-runner прогоняют сообщение
драйвера через редактирование строки подключения (`pgkit.redact_dsn`), потому
что yoyo и psycopg2 цитируют неразобранный DSN целиком — вместе с паролем.

## Фасад `DB` (`db.py`)

`DB` оборачивает пул и проксирует четыре метода — `execute`, `fetch`,
`fetchrow`, `fetchval`. Каждый вызов идёт не напрямую в пул, а через
`conn(pool)`: если в contextvar лежит активная транзакция (положенная
`Transactor.with_tx`), запрос выполняется в ней; иначе берётся соединение из
пула.

WHY: это делает репозиторий-код transaction-agnostic. Один и тот же
`db.execute(...)` атомарно попадает в текущую транзакцию, когда вызван внутри
`with_tx`, и работает автономно вне её — репозиторию не нужно знать, открыта
транзакция или нет, и не нужно прокидывать соединение отдельным аргументом.

`Executor` — структурный протокол этих четырёх методов: ему удовлетворяют и
`asyncpg.Pool`, и `asyncpg.Connection`, и фейк теста.

## `Transactor` (`transactor.py`)

```python
class Transactor(Protocol):
    async def with_tx[T](
        self, fn: Callable[[], Awaitable[T]], *, options: TxOptions | None = None
    ) -> T: ...
```

`PoolTransactor` реализует его поверх пула. Соединение кладётся в приватный
contextvar; извлекает его `conn()`.

Поведение `with_tx`:

- **Вложенность.** Если contextvar уже несёт транзакцию, вызов просто выполняет
  `fn()` в той же транзакции — без `BEGIN`/`COMMIT` и без savepoint. Опции
  вложенного вызова игнорируются: действуют опции внешней транзакции.
- **Изоляция.** `TxOptions(isolation=None)` — уровень сервера (`read committed`);
  `read_committed`, `repeatable_read`, `serializable`, `read_only`, `deferrable`
  задаются явно. Допустимые уровни — тип-алиас `pgkit.Isolation`.
- **Commit/Rollback.** Ошибка `fn` пробрасывается как есть (тип и `__cause__`
  сохраняются), транзакция откатывается. Сорвавшийся откат добавляется к
  исходному исключению примечанием (`BaseException.add_note`) и причину не
  подменяет — в Python нет `errors.Join`, а подмена типа сломала бы `except`
  вызывающего. Ошибки `BEGIN`/`COMMIT` — `pgkit.TransactionError` с префиксом
  `pgkit:`.
- **Отмена.** `asyncio.CancelledError` из `fn` обрабатывается тем же путём:
  транзакция откатывается, отмена летит дальше. Возврат соединения в пул
  дополнительно страхует `reset` asyncpg.

Вспомогательные функции:

- `conn(pool) -> Executor` — выбирает исполнителя запроса: транзакцию из
  contextvar или сам пул.
- `in_tx() -> bool` — сообщает, есть ли активная транзакция. Нужен коду, которому
  важна атомарность с бизнес-записью (outbox-insert, который обязан выполняться
  внутри той же транзакции, что и доменная запись).
- `with_test_tx()` / `reset_test_tx(token)` — маркер транзакции без соединения:
  `in_tx` его видит, `conn` игнорирует (запрос уходит в пул). Нужен фейкам
  `Transactor` (`testkit/pgtest`), чтобы код с гейтом на `in_tx` тестировался без
  Postgres.

## Маппинг ошибок (`errors.py`)

Предикаты-обёртки над SQLSTATE-кодами asyncpg, идущие по цепочке `__cause__`
(распознают ошибку, обёрнутую слоем выше):

| Предикат | Триггер |
|---|---|
| `is_no_rows(err)` | `asyncpg.NoDataFoundError` |
| `is_unique_violation(err)` | SQLSTATE `23505` |
| `is_foreign_key_violation(err)` | SQLSTATE `23503` |
| `is_check_violation(err)` | SQLSTATE `23514` |
| `is_not_null_violation(err)` | SQLSTATE `23502` |
| `is_retryable(err)` | SQLSTATE `40001` (serialization failure) или `40P01` (deadlock detected) |
| `is_lock_not_available(err)` | SQLSTATE `55P03` — `NOWAIT`/`SKIP LOCKED`-запрос или `lock_timeout` не дождались блокировки |
| `is_query_canceled(err)` | SQLSTATE `57014` — `statement_timeout` или `pg_cancel_backend` прервали запрос |

WHY по коду, а не по классу asyncpg: один предикат покрывает и неизвестные
подклассы с тем же sqlstate, и обёрнутую ошибку. Бизнес-код не лезет в строковые
SQLSTATE и спрашивает «это нарушение уникальности?» / «эту операцию можно
повторить?». `is_retryable` отделяет транзиентные конфликты (повторить с
backoff) от логических нарушений; `is_lock_not_available` нужен протоколам «кто
первый, тот и работает» (`FOR UPDATE NOWAIT`), `is_query_canceled` — чтобы
отличить срабатывание `statement_timeout` от прочих сбоев запроса.

Иерархия исключений кита: `PgkitError` — база; `ConfigError` (он же
`configkit.ConfigError`, то есть `ValueError`), `ConnectError`,
`TransactionError`, `MigrateError`, `InvalidUUIDError`.

## Тип jsonb

Кит **не** ставит на пул json/jsonb-кодек: asyncpg отдаёт такие колонки текстом,
и это единственная форма, в которой их видят все pgstore библиотеки
(`auditkit`, `outboxkit`). WHY: кодек изменил бы тип возврата любой
jsonb-колонки у каждого консьюмера разом, а выигрыш — один `json.loads`.
Разбор — `pgkit.unmarshal_jsonb(raw)` (пустое значение и NULL дают `None`).

## Migrate-runner

### Источник миграций (`migrate.py`)

```python
@dataclass(frozen=True, slots=True)
class Source:
    name: str   # ^[a-z][a-z0-9_]{0,44}$ — суффикс таблицы версий
    path: str   # каталог с NNNN_name.sql (+ NNNN_name.rollback.sql)

    def version_table(self) -> str  # "schema_migrations_" + name
```

Имя источника — не длиннее **45 символов**: `schema_migrations_<name>` должен
уложиться в 63 байта идентификатора Postgres. Нарушение регекспа, отсутствующий
каталог и каталог без `*.sql` — `pgkit.MigrateError` на прогоне.

У каждого источника **своя таблица версий** `schema_migrations_<name>` и своя
пара служебных таблиц yoyo (`_yoyo_version_<name>`, `_yoyo_log_<name>`). Версии
источников независимы: новая миграция `0002` в `auditkit` применяется как версия
2 источника `auditkit` и не затрагивает счётчик приложения. Каталог приложения —
источник `pgkit.AppSourceName` (`app`), таблица `schema_migrations_app`.

WHY не одна общая таблица: yoyo ведёт список применённых миграций на таблицу.
Склеивание нескольких источников в один список делает добавление миграции в кит
событием для приложения — порядок и содержимое каталогов перестают быть
независимыми. Таблица на источник делает каждый источник самостоятельным
append-only-логом. WHY служебные таблицы тоже на источник: yoyo определяет
версию своей внутренней схемы по наличию таблицы миграций, и для второго
источника общий `_yoyo_log` пересоздавался бы поверх существующего.

`migrate_up` / `migrate_down` (один шаг) / `migrate_version` работают с одним
источником. Все три синхронные: yoyo ходит в базу через psycopg2 — из async-кода
их зовут через `asyncio.to_thread`. Многооператорные файлы миграций yoyo режет
сам (`sqlparse`); файл с `CREATE INDEX CONCURRENTLY` обязан нести директиву
`-- transactional: false` первой строкой — иначе оператор попадёт в
транзакционный блок, где `CONCURRENTLY` запрещён.

`migrate_version` возвращает `NNNN`-префикс последней применённой миграции (0 —
ни одной). Понятия «dirty» у yoyo нет: каждая миграция применяется в собственной
транзакции и на ошибке откатывается целиком. Исключение — файлы с
`-- transactional: false`: сорвавшийся `CREATE INDEX CONCURRENTLY` оставляет
невалидный индекс, который чинят руками.

### Advisory-lock

`migrate_up_all(dsn, *sources, timeout=...)` применяет источники **по порядку**
под одним сессионным advisory-lock'ом `pg_advisory_lock` (ключ — FNV-1a от
строки `pylibinfra-migrate`) на отдельном psycopg2-соединении; lock снимается в
`finally`. Имена источников проверяются на уникальность. WHY: при нескольких
инстансах сервиса, стартующих одновременно, миграции должен применять ровно
один — остальные ждут на advisory-lock и находят схему уже применённой, а не
гонятся за неё.

Ожидание lock'а ограничено `timeout`: `pg_advisory_lock` сам ждёт бесконечно,
поэтому на время ожидания соединению ставится `statement_timeout`, и очередь
разрывается ошибкой `pgkit: migrate lock: timed out ...`. Инстанс, не
дождавшийся lock'а, падает на старте и уходит в рестарт — схему он не применяет.
Уже идущий SQL-оператор `timeout` не прерывает.

Текст `pgkit.MigrateError` проходит через редактирование DSN: yoyo цитирует
строку подключения целиком в `BadConnectionURI`, psycopg2 — в `invalid dsn`, и
без редактирования пароль уехал бы в стартовый лог сервиса.

### dishka: `PgMigrateProvider` и `MigrationSources`

Киты экспортируют свой источник как `<kit>.pgstore.MIGRATIONS`; консьюмер
собирает из них кортеж `pgkit.MigrationSources` в своём App-Provider'е.
`PgMigrateProvider` даёт пустой кортеж по умолчанию (override-паттерн),
сортирует источники по имени, добавляет последним каталог приложения из
`PG_MIGRATIONS_PATH` (если он не пуст) и применяет всё через `migrate_up_all` в
`asyncio.to_thread` под `PG_MIGRATE_TIMEOUT`. Приложение идёт последним, потому
что его схема может ссылаться на таблицы китов.

Порядок построения графа в dishka определяется зависимостями, а не порядком
регистрации провайдеров. Чтобы схема не зависела от него, `PgMigrateProvider`
провайдит маркер **`pgkit.Migrated`**: ресурс, которому нужна применённая схема
(партиции outbox, cleanup-джоба), объявляет `Migrated` зависимостью, а
консьюмер резолвит `Migrated` на старте до приёма трафика.

## Read-replica (`replica.py`)

```python
class ReadWriteDB:
    def read(self) -> DB   # реплика (или мастер, если реплики нет)
    def write(self) -> DB  # всегда мастер
    def has_replica(self) -> bool
```

Если реплика не задана (`ReadWriteDB(master, None)` или пустой
`PG_REPLICA_DSN`), `read()` тоже возвращает мастер — код read/write-split
пишется один раз и работает в обеих топологиях.

WHY роутинг explicit, а не автоматический: kit не парсит SQL и не решает за вас,
какой запрос куда. Потребитель сам выбирает `rw.read()` для запросов, которые
терпят репликационный лаг, и `rw.write()` — для всего остального (включая
read-after-write в рамках одной операции).

## Health-пробы (`probe.py`)

`probe(db)` (имя `pgkit`) выполняет `SELECT 1` на пуле primary,
`replica_probe(rw)` (имя `pgkit/replica`) — на пуле реплики; без реплики вторая
проходит без запроса, потому что мастер уже покрыт первой. Обе ошибки
префиксуются именем кита. Консьюмер складывает пробы в `healthkit.Probes`,
httpkit отдаёт их в `/ready`.

## dishka-композиция (`module.py`)

`PgProvider` строит граф:

- `pool` — primary `asyncpg.Pool`: `new_pool` + `instrument_pool(pool, "primary")`;
  на выходе снимает регистрацию метрик и закрывает пул.
- `db` — фасад `DB` поверх пула.
- `transactor` — `PoolTransactor` под протоколом `Transactor`.
- `replica_pool` — пул реплики или `None`: при пустом `PG_REPLICA_DSN` реплики
  нет, иначе поднимается пул с DSN и `PG_REPLICA_MAX_CONNS` реплики (остальное
  наследуется от `PoolConfig`) и инструментируется как `role=replica`. dishka
  проверяет граф целиком на сборке контейнера, поэтому «опционально» выражено
  типом-обёрткой `OptionalReplicaConfig = ReplicaConfig | None`: `PgProvider`
  даёт `None`, а сервис с репликой перекрывает дефолт своим провайдером.
- `read_write_db` — `ReadWriteDB` из мастера и реплики.

Метрики идут в глобальный Meter (`_internal/otelkit`): без `MeterProvider`
инструменты — no-op, граф при этом поднимается. Config'и `PgProvider` не
provide'ит — это делает caller, единообразно с остальными китами.
`PgMigrateProvider` — отдельный провайдер (см. выше), ему нужны `PoolConfig` и
`MigrateRunnerConfig`, а провайдит он `pgkit.Migrated`.
