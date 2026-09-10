# Конфигурация outboxkit

Конфиги читаются из ENV: `outboxkit.new_config()` (orchestration-слой) и
`pgstore.new_config()` (Postgres-store). **Имена полей и их `validation_alias` —
part of public API**: их изменение — breaking change (minor-релиз, `!` в
Conventional Commit, строка миграции в аннотации тега). Ни `OutboxProvider`, ни
`OutboxPgstoreProvider` конфиги не provide'ят — их регистрирует консьюмер.
Невалидное значение валит старт с `outboxkit.ConfigError`.

**Заданная, но пустая переменная равна незаданной** — берётся дефолт кита. Все
длительности — **секунды** (`float`).

## Справочник ENV

`outboxkit.Config` (`outboxkit.new_config`):

| Env | Поле | Тип | Default | Назначение |
|---|---|---|---|---|
| `OUTBOX_BATCH_SIZE` | `batch_size` | `int` (`>= 1`) | `100` | Сколько строк `Worker` вычитывает за тик (`LIMIT`). |
| `OUTBOX_INTERVAL` | `interval` | `float, секунды` (`> 0`) | `1` | Период тика (интервал `timerkit.Job`). |
| `OUTBOX_PUBLISH_MODE` | `publish_mode` | `batch` \| `ordered` | `batch` | Режим публикации (см. ниже). Иное значение — `ConfigError`. |
| `OUTBOX_MAX_ATTEMPTS` | `max_attempts` | `int` (`>= 0`) | `0` | Сколько попыток публикации даётся строке до пометки dead. `0` — без ограничения. |
| `OUTBOX_TICK_TIMEOUT` | `tick_timeout` | `float, секунды` (`> 0`) | `30` | Дедлайн тика: выборка + публикация. `0` — `ConfigError`. |
| `OUTBOX_RETRY_BACKOFF` | `retry_backoff` | `float, секунды` (`>= 0`) | `1` | Пауза перед первым ретраем строки; каждая следующая неудача удваивает её. `0` — ретрай ближайшим тиком. |
| `OUTBOX_RETRY_MAX_BACKOFF` | `retry_max_backoff` | `float, секунды` (`>= retry_backoff`) | `300` | Потолок паузы между попытками. `0` — без потолка. |

`pgstore.Config` (`pgstore.new_config`):

| Env | Поле | Тип | Default | Назначение |
|---|---|---|---|---|
| `OUTBOX_RETENTION_MONTHS` | `retention_months` | `int` (`>= 0`) | `0` | Сколько месяцев хранить партиции; `retention_keeper_job` дропает старше. `0` — не дропать. |
| `OUTBOX_REQUIRE_TX` | `require_tx` | `bool` | `true` | `insert` вне транзакции — `outboxkit.NoTxError` (tx-strict guard). |

`outboxkit` не имеет собственного эндпоинта брокера или БД. Публикация в
JetStream требует `natskit` с `NATS_JETSTREAM_ENABLED=true` и стримом,
покрывающим subject'ы outbox'а; хранилище — тот же Postgres (14+), что `PG_DSN`.

## Тюнинг publish-mode

- `batch` — максимальная пропускная способность: один round-trip на тик, реплики
  дренируют очередь параллельно. Дефолт; подходит, когда потребители не зависят
  от порядка событий.
- `ordered` — FIFO по очереди ценой round-trip на каждое сообщение, стопа всего
  тика на первой ошибке и одного активного воркера на таблицу (остальные реплики
  пропускают тик, пока голова очереди занята). Брать, только когда порядок
  доставки — жёсткое требование домена; про границы гарантии — раздел «Порядок
  публикации» в [architecture.md](architecture.md).

`OUTBOX_BATCH_SIZE` и `OUTBOX_INTERVAL` балансируют latency и нагрузку: меньший
интервал и больший батч — ниже задержка публикации, выше частота запросов к БД и
брокеру.

## Тюнинг tick-timeout

`OUTBOX_TICK_TIMEOUT` ограничивает выборку и ожидание `PubAck`; на фазу
mark/commit сверху отводится ещё 5 секунд, и она идёт мимо отмены вызывающего
(см. § Lifecycle в [architecture.md](architecture.md)). Нулевое значение
отвергается на старте: «без дедлайна» означало бы транзакцию, которую не
обрывает ни остановка сервиса, ни таймер — только серверные
`statement_timeout`/`idle_in_transaction_session_timeout`.

Значение обязано быть **меньше** `PG_IDLE_IN_TX_SESSION_TIMEOUT` (`pgkit`,
default `60s`): во время ожидания брокера транзакция простаивает, и Postgres
оборвёт сессию по этому лимиту. Таймаут ожидания `PubAck` внутри async-батча
задаётся на стороне `natskit` (`NATS_JS_PUBLISH_ASYNC_TIMEOUT`) — `outboxkit`
только ограничивает тик целиком.

## Тюнинг max-attempts и backoff

- `OUTBOX_MAX_ATTEMPTS = 0` (default) — строка ретраится бесконечно. Уместно,
  когда сбой публикации — это временная недоступность брокера и терять событие
  нельзя.
- `OUTBOX_MAX_ATTEMPTS = N > 0` — после `N` неудачных попыток строка помечается
  dead (`dead_at`), перестаёт вычитываться и не блокирует очередь (важно для
  `ordered`). Эмитится `outbox_events_dead_total`; вернуть строки в очередь —
  `Outbox.revive_dead` (см. [usage.md](usage.md)).
- Пауза между попытками растёт экспоненциально: `OUTBOX_RETRY_BACKOFF ×
  2^attempts`, но не выше `OUTBOX_RETRY_MAX_BACKOFF`. С дефолтами (`1s`, `5m`)
  десять попыток растянуты примерно на 25 минут, а не на 10 секунд. Планируя
  `OUTBOX_MAX_ATTEMPTS`, считайте бюджет ожидания как сумму пауз, а не
  `N × OUTBOX_INTERVAL`.
- `OUTBOX_RETRY_BACKOFF=0` выключает паузу: строка возвращается в выборку
  ближайшим тиком.

## Тюнинг retention

`OUTBOX_RETENTION_MONTHS` управляет `retention_keeper_job` (раз в сутки):
дропаются партиции, чей месяц старше `OUTBOX_RETENTION_MONTHS` до текущего и в
которых нет неопубликованных (не-dead) событий; партиции с такими событиями
остаются и перечисляются в `pgstore.PendingRowsError` (его логирует `timerkit`).
`0` — retention выключен, партиции живут вечно (растут).

Партиции текущего и следующего месяца создаёт `OutboxPgstoreProvider` при сборке
store'а и `partition_keeper_job` (на старте и раз в сутки), чтобы insert не падал
на смене календаря.

## Миграции и таблицы

Источник `pgstore.MIGRATIONS` (`pgkit.Source(name="outboxkit")`, таблица версий
`schema_migrations_outboxkit`) содержит миграции таблицы `outbox`; консьюмер
кладёт его в кортеж `pgkit.MigrationSources`, который применяет
`PgMigrateProvider` на старте. Схема таблицы:

```sql
CREATE TABLE outbox (
    id              BIGSERIAL    NOT NULL,
    subject         TEXT         NOT NULL,
    payload         JSONB        NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    published_at    TIMESTAMPTZ,
    attempts        INT          NOT NULL DEFAULT 0,
    last_error      TEXT,
    next_attempt_at TIMESTAMPTZ,
    dead_at         TIMESTAMPTZ,
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
```

Плюс partial-индекс `outbox_unpublished_idx ON (created_at, id) WHERE
published_at IS NULL AND dead_at IS NULL` и партиции на текущий месяц + 3 вперёд.

| Колонка | Назначение |
|---|---|
| `id` | `BIGSERIAL`; уходит как `Nats-Msg-Id` (с namespace в multi-instance). |
| `subject` | NATS-subject публикации; label метрик. |
| `payload` | JSON события (сериализует `outboxkit.Bus`). |
| `created_at` | Время начала транзакции вставки (`NOW()` базы); ключ партиционирования и `ORDER BY`. |
| `published_at` | Время `mark_published`; `NULL` — не опубликовано. |
| `attempts` | Счётчик неудачных попыток публикации. |
| `last_error` | Текст последней ошибки (обрезается до 1000 символов). |
| `next_attempt_at` | Момент, раньше которого строка не вычитывается (backoff); `NULL` — готова сейчас. |
| `dead_at` | Время пометки dead; `NULL` — не dead. |

Таблица инстанса заводится тем же набором миграций:
`pgstore.table_migrations(table)` возвращает `pgkit.Source` для произвольного
имени (таблица версий `schema_migrations_<table>`) — консьюмер кладёт его в
`pgkit.MigrationSources`. DDL идемпотентен (`IF NOT EXISTS`), поэтому источник
применяется и поверх таблицы, заведённой вручную: недостающие колонки доедут
миграцией. Имя таблицы валидируется (`^[a-z][a-z0-9_]{0,62}$`) — оно инлайнится в
SQL и не передаётся плейсхолдером; для источника миграций имя должно укладываться
в 45 символов (ограничение `pgkit.Source.name`). `pgstore.schema_sql(table)`
отдаёт тот же DDL строкой — для собственной миграции приложения.
