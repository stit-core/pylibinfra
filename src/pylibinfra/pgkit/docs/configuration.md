# pgkit — конфигурация

Все значения читаются из окружения через pydantic-settings. `PgProvider` и
`PgMigrateProvider` сами Config'и **не** provide'ят — caller регистрирует их
фабрики (`new_pool_config`, `new_replica_config`, `new_migrate_runner_config`)
или собирает App-Provider (см. [architecture.md § dishka-композиция](architecture.md)).

```python
@provide
def pool_config(self) -> PoolConfig:
    return new_pool_config()
```

Каждый `new_*_config` валидирует прочитанное и падает на старте: нарушение
инварианта — `pgkit.ConfigError` (подкласс `ValueError`). Те же проверки
выполняет прямая сборка `PoolConfig.model_validate({...})` — она поднимает
pydantic `ValidationError` (тоже `ValueError`), потому что минует
`configkit.build`.

## `PoolConfig` (`pool.py`)

Минимум для старта — `PG_DSN`. Остальное имеет рабочие default'ы. Длительности —
**секунды (float)**.

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `PG_DSN` | `dsn` | `SecretStr` | — (обязателен) | строка подключения к primary Postgres; непустая, пароль не раскрывается ни в логах, ни в текстах ошибок кита |
| `PG_MAX_CONNS` | `max_conns` | `int` | `10` | максимум соединений в пуле; `>= 0`, `0` — дефолт asyncpg |
| `PG_MIN_CONNS` | `min_conns` | `int` | `2` | минимум тёплых соединений; `>= 0`, `0` — дефолт asyncpg; при `PG_MAX_CONNS > 0` должен быть `<= PG_MAX_CONNS` |
| `PG_MAX_CONN_IDLE_TIME` | `max_conn_idle_time` | `float`, секунды | `300` | простой, после которого idle-соединение закрывается; `>= 0`, `0` — дефолт asyncpg |
| `PG_CONNECT_TIMEOUT` | `connect_timeout` | `float`, секунды | `10` | таймаут установки каждого физического соединения и стартового ping; `>= 0`, `0` — без лимита кита (действует `connect_timeout` из DSN) |
| `PG_OTEL_TRACE` | `otel_trace` | `bool` | `true` | включать ли `AsyncPGInstrumentor` (per-query спаны) |
| `PG_STATEMENT_TIMEOUT` | `statement_timeout` | `float`, секунды | `30` | `statement_timeout` сессии; `>= 0`, `0` — без лимита |
| `PG_IDLE_IN_TX_SESSION_TIMEOUT` | `idle_in_tx_session_timeout` | `float`, секунды | `60` | `idle_in_transaction_session_timeout` сессии; `>= 0` |
| `PG_LOCK_TIMEOUT` | `lock_timeout` | `float`, секунды | `5` | `lock_timeout` сессии; `>= 0` |

Ноль в размерах и таймаутах пула означает **дефолт asyncpg**, а не
«выключено». Сессионные таймауты переводятся в миллисекунды и уезжают в
StartupMessage через `server_settings`. Детали — в
[architecture.md § Пул соединений](architecture.md).

Переменных под max-lifetime соединения и фоновый health-check пула у кита нет:
asyncpg не умеет ни плановой ротации по возрасту соединения, ни периодической
проверки простаивающих соединений.

**Секрет в ошибках.** Неразобранный DSN драйверы цитируют целиком (yoyo —
`No scheme specified in connection URI '…'`, psycopg2 — `invalid dsn: missing
"=" after "…"`), поэтому `new_pool` и migrate-runner редактируют сообщение
прежде, чем поднять `pgkit.ConnectError`/`pgkit.ConfigError`/`pgkit.MigrateError`.
Та же функция доступна потребителю: `pgkit.redact_dsn(dsn)` →
`postgresql://app:xxxxx@db:5432/app`.

## `ReplicaConfig` (`replica.py`)

Опциональная read-replica. Если `PG_REPLICA_DSN` пуст — реплики нет, `read()`
идёт на мастер. Регистрировать конфиг в контейнере нужно только при наличии
реплики, и делается это через тип-обёртку `OptionalReplicaConfig`: `PgProvider`
даёт дефолт `None`, консьюмер перекрывает его своим провайдером.

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `PG_REPLICA_DSN` | `replica_dsn` | `SecretStr` | `""` | строка подключения к read-replica; пусто → реплики нет |
| `PG_REPLICA_MAX_CONNS` | `max_conns` | `int` | `10` | максимум соединений в пуле реплики; `>= 0`, `0` — дефолт asyncpg |

Пул реплики наследует от `PoolConfig` все остальные параметры (`min_conns`,
idle-time, `connect_timeout`, `otel_trace`, сессионные таймауты); собственными у
реплики являются только DSN и лимит соединений. Из наследования следует
кросс-инвариант: при `PG_REPLICA_MAX_CONNS > 0` он должен быть `>= PG_MIN_CONNS`
— иначе унаследованный прогрев не помещается в пул реплики. Нарушение валит
старт: `PG_REPLICA_MAX_CONNS (1) must be >= PG_MIN_CONNS (2)`.

## `MigrateRunnerConfig` (`migrate.py`)

Используется `PgMigrateProvider` для прогона миграций на старте приложения.

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `PG_MIGRATIONS_PATH` | `migrations_path` | `str` | `/migrations` | каталог `NNNN_name.sql` приложения (источник `app`); пустая строка — у приложения нет своих миграций, применяются только источники китов; окружающие пробелы отвергаются |
| `PG_MIGRATE_TIMEOUT` | `migrate_timeout` | `float`, секунды | `120` | лимит на коннект и ожидание advisory-lock'а при прогоне; `> 0` |

DSN для миграций `PgMigrateProvider` берёт из `PoolConfig.dsn` (тот же
`PG_DSN`). Пустой `PG_MIGRATIONS_PATH` читается буквально: у этой модели
`env_ignore_empty` выключен, поэтому «переменная задана пустой» означает «своих
миграций нет», а не «взять дефолт».

**Что `PG_MIGRATE_TIMEOUT` не режет.** Он ограничивает коннект и ожидание
advisory-lock'а, но не прерывает уже идущий SQL-оператор: yoyo не отменяет
запрос по дедлайну. Долгая миграция (`CREATE INDEX` на большой таблице) идёт
столько, сколько нужно, и старт ждёт её.

## Public API

`pgkit.PoolConfig`, `pgkit.ReplicaConfig` и `pgkit.MigrateRunnerConfig` — **part
of public API**. Переименование полей, смена env-alias'ов или типов = breaking
change (см. корневой [README § Версионирование](../../../../README.md)).

## Тюнинг пула

- **`PG_MAX_CONNS` / `PG_MIN_CONNS`.** Потолок параллелизма к БД на инстанс
  сервиса; при N инстансах суммарная нагрузка ≈ `N × PG_MAX_CONNS` — сверяй с
  `max_connections` сервера. `PG_MIN_CONNS` держит тёплые соединения, чтобы не
  платить за коннект на пике.
- **`PG_MAX_CONN_IDLE_TIME`.** Ротация простаивающих соединений; помогает при
  балансировщиках/PgBouncer и утечках состояния сессии.
- **`PG_CONNECT_TIMEOUT`.** Ограничивает зависание на недоступном хосте (SYN без
  ответа, TLS-handshake): стартовый ping и каждый реконнект пула завершаются
  ошибкой, а не ждут TCP-таймаута ОС.
- **`PG_STATEMENT_TIMEOUT`.** Защита от зависших запросов. Длинные операции
  (батч-джобы) могут упереться в него — для них поднимай таймаут на уровне
  сессии внутри транзакции или заводи отдельный пул. Мигратор открывает
  собственное соединение и этот лимит не наследует.
- **`PG_IDLE_IN_TX_SESSION_TIMEOUT`.** Обрывает транзакции, которые открыли и
  забыли (висящая транзакция держит блокировки и тормозит autovacuum). Код,
  который держит транзакцию, ожидая внешнюю систему, обязан укладываться в лимит.
- **`PG_LOCK_TIMEOUT`.** Ограничивает ожидание блокировки; небольшое значение
  превращает дедлок-подобные ожидания в быструю ошибку `55P03`
  (`is_lock_not_available`), которую можно повторить.
- **PgBouncer.** Сессионные таймауты уезжают в StartupMessage соединения. В
  `transaction`-режиме PgBouncer мультиплексирует серверные соединения, и эти
  параметры на них не гарантируются — задавай их на стороне пулера или сервера.
- **`PG_OTEL_TRACE`.** Оставляй `true` в проде — это источник per-query спанов. В
  бенчмарках можно выключить, чтобы убрать оверхед трейсинга.
