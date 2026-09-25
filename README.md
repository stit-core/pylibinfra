<div align="center">

# pylibinfra

### Инфраструктурные Python-kit'ы с observability из коробки

Набор переиспользуемых Python-библиотек для сервисов экосистемы stit:
auth, БД, messaging, кэш, аудит, storage, Telegram-бот. Каждый kit —
`dishka.Provider`, инструментирован OTel, подключается независимо. Импортируется
как Python-пакет из бизнес-репозиториев. Структурный аналог
[`golibinfra`](https://git.stit.tech/stit-core/golibinfra) для Python-стека:
те же имена китов, ENV-переменные и метрики.

[![License](https://img.shields.io/badge/license-Apache--2.0-2563eb)](LICENSE)
[![Часть stit](https://img.shields.io/badge/stit-открытые_шаблоны-6366f1)](https://git.stit.tech/stit-core/stit-public)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB)](#использование-в-проекте)
[![Observability](https://img.shields.io/badge/observability-OTel_by_default-8b5cf6)](#observability)

</div>

```python
from pylibinfra import healthkit, httpkit, pgkit, testkit
```

## Где это в экосистеме

`pylibinfra` — общая Python-lib экосистемы stit (7 форкаемых шаблонов + 2 общие
библиотеки: эта и `golibinfra` для Go-сервисов). Её импортирует
[`telegram-bot-template`](https://git.stit.tech/stit-core/telegram-bot-template)
(форк `<org>/<bot>`); `httpkit` и `grpckit` рассчитаны и на Python-сервисы на
FastAPI/gRPC. Библиотека не форкается: потребители тянут пакет из git по тегу, а
выкатка их образов идёт из `<org>/infra`. Карта репо и операционный флоу —
[`docs/reference/ecosystem.md`](https://git.stit.tech/stit-core/project-infra-template-compose/src/branch/main/docs/reference/ecosystem.md)
в `project-infra-template-compose`; витрина всех репо —
[stit-public](https://git.stit.tech/stit-core/stit-public).

## Доступ к репозиторию

Репо лежит на собственной Gitea (`git.stit.tech`), не на публичном PyPI. Установка
из git напрямую через uv/pip по тегу:

```bash
uv add "pylibinfra @ git+https://git.stit.tech/stit-core/pylibinfra.git@vX.Y.Z"
```

Для авторизованного доступа — git-аутентификация SSH-ключом или токеном
(`git config --global url."git@git.stit.tech:".insteadOf "https://git.stit.tech/"`,
либо `~/.netrc` с токеном для CI). Extras: `[testkit]` (testcontainers/pytest для
интеграционных тестов консьюмера), `[tgbotkit]` (aiogram), `[grpckit]` (grpcio).

## Использование в проекте

```toml
# pyproject.toml
[project]
dependencies = [
  "pylibinfra @ git+https://git.stit.tech/stit-core/pylibinfra.git@vX.Y.Z",
]
```

`uv lock && uv sync` подтянет. Требуется Python ≥ 3.12.

## Принципы

- **Никакой продуктовой специфики.** Если модуль знает про конкретные роли, домены,
  имена сервисов — он не здесь.
- **Без скрытых зависимостей.** Каждый пакет подключается независимо; `Config`
  даёт caller, `new_config()` валидирует и падает на старте (`<kit>.ConfigError`).
- **dishka-first.** Каждый пакет экспортирует `Provider` для проводки.
- **Observability-by-default.** Все компоненты инструментированы OTel.
- **Безопасные дефолты.** Таймауты у каждого исходящего вызова, retry только
  идемпотентных запросов, `OUTBOX_REQUIRE_TX=true`, deny-by-default auth в httpkit,
  редактирование секретов в логах и текстах ошибок.
- **async-first.** Весь I/O — asyncio (asyncpg / nats-py / redis.asyncio / httpx /
  FastAPI / grpc.aio); CPU-тяжёлое (Argon2) — в `asyncio.to_thread`.

## Пакеты

У каждого кита — собственный `README.md` рядом с его кодом
(`src/pylibinfra/<kit>/README.md`): назначение, быстрый старт, конфигурация,
observability, API, инварианты. Это и есть единый источник правды по киту. У
сложных китов (`authnkit`, `grpckit`, `httpkit`, `natskit`, `outboxkit`, `pgkit`,
`tgbotkit`) сверх README есть каталог `docs/` (`architecture.md`,
`configuration.md`, `observability.md`, `usage.md`, `testing.md`). Таблица ниже —
тонкий индекс: ответственность + ссылка на README кита.

| Пакет | Ответственность |
|---|---|
| [`pgkit`](src/pylibinfra/pgkit/) | Фасад asyncpg: пул с сессионными таймаутами и стартовым ping, `DB`, `Transactor` (contextvars, `TxOptions`), migrate-runner (yoyo, advisory-lock, таблица версий на источник — `Source`, `PgMigrateProvider`, `MigrationSources`, маркер `Migrated`), предикаты pg-ошибок, `probe`/`replica_probe`, `ReadWriteDB` (`PG_REPLICA_DSN`, опциональна); `pg_pool_*{role}` |
| [`storagekit`](src/pylibinfra/storagekit/) | S3-совместимое хранилище поверх aioboto3: порт `Storage` (`put`/`get`/`stat`/`delete`), `Presigner` (presigned GET/PUT, TTL 1 s…7 d), `URLBuilder` (`path`/`virtual`), `new_key` (uuidv7 + ext), `MemoryStorage` и контрактный набор `testkit/storagetest`; подпакет `s3` — клиент (SigV4, retries/timeouts botocore), multipart по `STORAGE_S3_PART_SIZE`, проба `storagekit`, `S3Provider`; `storage_ops_*` |
| [`httpkit`](src/pylibinfra/httpkit/) | FastAPI/uvicorn-сервер с цепочкой RequestID → OTel → AccessLog → Metrics → Recovery → SecurityHeaders → CORS → RateLimit → MaxBody → Idempotency → Auth; `APIError` (problem+json) + `map_err`/`map_is`/`map_as`, нейтрализованные 5xx; `/health` + `/ready` по `healthkit.Probes` (параллельно, `HTTP_READY_PROBE_TIMEOUT`); отключаемые `/docs` (`HTTP_DOCS_ENABLED`); синхронный `listen`, graceful `stop`; `client_ip`, `MemoryStore`, `dishka_lifespan`, `shared_secret_guard`; точки расширения `Middlewares`/`OptionalSecuritySchemes` |
| [`grpckit`](src/pylibinfra/grpckit/) | grpc.aio-сервер (как httpkit для FastAPI): `Server` (bind TCP и/или UDS, `listen`/`serve`/`stop`), цепочка интерсепторов request-id → access-log (+RED-метрики) → recovery → опц. auth (порт `Authenticator`), стандартный `grpc.health.v1` с переоценкой по `healthkit.Probes` (`GRPC_HEALTH_INTERVAL`, `GRPC_HEALTH_PROBE_TIMEOUT`), TLS, опц. reflection; сервисы через `Services`, расширение через `HandlerInterceptor`; `testkit/grpctest`. **extra `[grpckit]`** |
| [`natskit`](src/pylibinfra/natskit/) | Pub/sub поверх NATS: Core-`Bus` (fan-out + queue-группы) и JetStream-`StreamBus` (персист, дедуп по `Nats-Msg-Id`, durable-консьюмеры, DLQ/`MaxDeliver`/NAK-backoff, deadline + `in_progress`); `Subscription` (`unsubscribe`/`drain`), graceful `close` (`NATS_DRAIN_TIMEOUT`), backpressure (`NATS_MAX_IN_FLIGHT`), аутентификация (`NATS_CREDS_FILE`/`NATS_NKEY_SEED_FILE`/`NATS_TLS_CA_FILE`), редактирование userinfo в логах, проба `natskit`; несколько шин — dishka-компоненты + `<NAME>_NATS_*`; фейк `testkit/natstest` |
| [`httpclient`](src/pylibinfra/httpclient/) | HTTP-клиент поверх httpx: retry идемпотентных запросов с backoff и `Retry-After` (`parse_retry_after`), circuit breaker на upstream-хост, политика редиректов (лимит + запрет кросс-хоста), SSE-стримы (WHATWG), OTel-спаны и `X-Request-ID` из контекста; `new_config_prefixed` для нескольких клиентов; `ApiClient` — JSON-слой: декод в wire-модель (`model=`), таксономия typed-ошибок, `Idempotency-Key`, именованные бэкенды (`ApiClients` из `BackendConfig`); `http_client_*` |
| [`cachekit`](src/pylibinfra/cachekit/) | Redis ≥ 7.0 поверх redis.asyncio (major сверяется на старте, `REDIS_SKIP_VERSION_CHECK`): `Cache` (TTL, `set_nx`/`get_del`/`compare_and_delete`, теги), `get_json`/`set_json`, `IdempotencyStore` для `httpkit`, `MemoryCache` (тесты и single-replica), префикс ключей (`REDIS_KEY_PREFIX`), проба `cachekit`; `cache_commands_*` и спаны `redis <cmd>` |
| [`healthkit`](src/pylibinfra/healthkit/) | Именованная проба `Probe(name, check)` (`new`, `with_timeout`, `run`) и DI-кортеж `healthkit.Probes`; `/ready` в `httpkit` и health-loop `grpckit` собирают пробы китов |
| [`flagkit`](src/pylibinfra/flagkit/) | Feature flags из ENV (`FEATURE_FLAGS=foo=true,bar=baz`): `Store.bool/str/names`, строгий разбор — невалидная запись роняет старт |
| [`timerkit`](src/pylibinfra/timerkit/) | Периодические job'ы поверх asyncio: `Job` (interval/timeout/jitter/run_on_start), кортеж `Jobs` от консьюмера, `Runner` с валидацией на старте и ожиданием запусков на остановке; расписание по `clockkit.Clock`; спан `timerkit.job <name>`, `timer_job_*` |
| [`telemetry`](src/pylibinfra/telemetry/) | Opt-in bootstrap OTel SDK: tracer/meter/logger providers + OTLP HTTP-экспортёры (`OTEL_EXPORTER_OTLP_ENDPOINT` — полный URL, `OTEL_SDK_DISABLED` — общий выключатель, Bearer/заголовки из env), resource-атрибуты `service.*`, опц. `tenant`, composite-propagator, system-метрики; `LoggerProvider` для `logkit`, ошибки экспорта — в `logkit` с троттлингом; `telemetry.start(container)` поднимает SDK первым. Без него kit-инструментация no-op |
| [`logkit`](src/pylibinfra/logkit/) | `Logger`: JSON или text (`LOG_FORMAT`) в stdout (`LOG_SILENT`) или writer + OTel Logs sink, PII-redact на уровне полей (дефолты знают Bearer/Basic/JWT/API-ключи), `request_id`/`trace_id`/`span_id` в каждой записи, `register_attr`, мост stdlib-`logging` (`capture_stdlib`); `log_messages_total`; capture-логгер `testkit/logtest` |
| [`authnkit`](src/pylibinfra/authnkit/) | Кирпичики аутентификации: `Hasher` (Argon2id в `asyncio.to_thread`, строгий PHC с окном параметров и потолком стоимости, `needs_rehash`, `verify_dummy`); `HS256JWT` (pin HS256, обязательный `exp`, `typ` access/refresh, `JWT_LEEWAY`, `iss`/`aud`, часы через `Clock`); `TOTP` (RFC 6238, окно `TOTP_SKEW`, `verify_once` поверх `UsedCodeStore`); экземплярный RBAC `Registry`; `authn_*`; `testkit/authntest` |
| [`bizmetric`](src/pylibinfra/bizmetric/) | Helper бизнес-метрик: `counter`/`histogram`/`gauge`/`up_down_counter` + `must_name`, форсит конвенцию `biz_<domain>_<event>_*` (исключение при нарушении), общие бакеты у гистограмм |
| [`outboxkit`](src/pylibinfra/outboxkit/) | Transactional outbox: `Bus` пишет событие в транзакции домена, `Worker` тиком-транзакцией (list → publish → mark) публикует в JetStream (`batch` — SKIP LOCKED между репликами, `ordered` — один активный воркер); backoff в колонке `next_attempt_at`, poison→dead + `revive_dead`, partition-keeper и retention через `DETACH … CONCURRENTLY` (Postgres ≥ 14), tx-strict insert (`OUTBOX_REQUIRE_TX=true`), `BacklogReporter`; несколько инстансов — `OutboxInstanceProvider` + `pgstore.OutboxPgstoreFactoryProvider`; `outbox_*{instance}`; recorder — `testkit/outboxtest` |
| [`auditkit`](src/pylibinfra/auditkit/) | Append-only audit-log: `Entry`/`Logger` с обязательными `action`/`resource` и лимитами полей, core+pgstore split, запись участвует в транзакции из contextvar, время — часы БД, триггер против UPDATE/DELETE; `audit_log_*`; `testkit/audittest` |
| [`bruteforcekit`](src/pylibinfra/bruteforcekit/) | Brute-force guard: атомарный счётчик попыток по паре (subject, канонический IP), `Guard` + `Cleaner`, `LockState(until, failed_count)`, новое окно после истечения lock'а, cleanup-джоба для `timerkit.Jobs`; `bruteforce_*`; `testkit/bruteforcetest` |
| [`clockkit`](src/pylibinfra/clockkit/) | `Clock` (`now`/`since`/`sleep`) + фейки `Fixed`/`Tickable` (будят спящих при `advance`/`set`) для детерминированных временных тестов; `ClockProvider` |
| [`idkit`](src/pylibinfra/idkit/) | Внутренние id: `new_v7()` — UUID v7 строкой (time-sortable, строго возрастающий в процессе), `validate`/`is_v7` — проверка канонической формы |
| [`config`](src/pylibinfra/config/) | Загрузчик App-конфига: ENV-слой pydantic-settings + композиция `Source` (`env_prefixed`); ошибки всех источников в одном `ConfigError` без значений полей |
| [`tgbotkit`](src/pylibinfra/tgbotkit/) | aiogram-bootstrap (как httpkit для FastAPI): `BotProvider` (Bot/Dispatcher/RedisStorage/runner), цепочка `dp.update` (request-id → дедуп `update_id` → per-user троттлинг → recovery → RED+спан), session-middleware исходящих (GCRA-лимит, порядок в чате, ретрай `429`), polling\|webhook `BotRunner` поверх `httpkit.Server(autostart=False)` с graceful-стопом; точки расширения `RouterBundle`/`CommandBundle`/`NotifierBundle`/`ErrorUX`; подсистемы i18n (fluent)/notifications (JetStream→Telegram)/payments (XTR+фиат)/webapp (`initData`); пробы `tgbotkit`, `tgbotkit/redis`; `testkit/bottest`. **extra `[tgbotkit]`** |
| [`testkit`](src/pylibinfra/testkit/) | testcontainers: `start_postgres(migrations=…)`, `start_nats(jetstream=…)`, `start_redis`, `start_minio`, `require_docker`; сабпакеты `testkit/<name>test` — `audittest`, `authntest`, `bottest`, `bruteforcetest`, `grpctest`, `handlertest`, `logtest`, `natstest`, `outboxtest`, `pgtest`, `storagetest` |

Служебные модули `pylibinfra._internal` (`otelkit` — scope `pylibinfra.<kit>`,
версия инструментации, `meter`/`tracer` и две общие шкалы bucket-границ для
гистограмм длительности; `requestid` — contextvar request-id, общий для
транспортов и клиентов; `configkit` — база `KitSettings`, `ConfigError`, `build`)
наружу не экспортируются.

## Per-concern endpoints (multi-host)

Каждый кит читает свой эндпоинт из ENV — **разное значение = разный хост**. Это
«шов» для split-топологии: storage / Postgres / NATS / Redis можно
развести по отдельным серверам, **не трогая код**. Репликация / HA / failover —
дело потребителя.

Таблица полная: `make envdoc-check` требует, чтобы каждая endpoint-переменная
китов (`*_URL`, `*_DSN`, `*_ENDPOINT`, `*_ADDR`, `*_TARGET`) была здесь, а каждая
переменная отсюда существовала в коде. Остальные настройки кита — в его README
§ Конфигурация.

### Инфраструктура

| Env | Кит | Дефолт | Назначение |
|---|---|---|---|
| `PG_DSN` | `pgkit` | — (обязателен) | Postgres (primary); `PG_CONNECT_TIMEOUT` (`10`) — таймаут стартового подключения |
| `PG_REPLICA_DSN` | `pgkit` | — (опц.) | Postgres read-replica (`ReadWriteDB`); пусто → реплики нет |
| `NATS_URL` | `natskit` | `nats://localhost:4222` | NATS / JetStream; userinfo в логах редактируется; именованный инстанс читает `<NAME>_NATS_URL` |
| `REDIS_URL` | `cachekit` | `redis://localhost:6379/0` | Redis ≥ 7.0 (cache + idempotency store); major сверяется на старте — `REDIS_SKIP_VERSION_CHECK=true` снимает проверку |
| `STORAGE_S3_ENDPOINT` | `storagekit` | — (обязателен) | S3 / MinIO: `host[:port]` или `scheme://host[:port]` |
| `STORAGE_PUBLIC_BASE_URL` | `storagekit` | — (опц.) | база публичных ссылок на объекты (CDN или публичный хост бакета); пусто → `URLBuilder` отдаёт относительный `/<bucket>/<key>` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `telemetry` | `http://localhost:4318` | OTLP HTTP-коллектор, полный URL по спецификации (per-signal `/v1/{traces,metrics,logs}`); токен — `OTEL_TOKEN` |
| `OTEL_SDK_DISABLED` | `telemetry` | `false` | общий выключатель SDK: `true` → провайдеры no-op, экспортёры не поднимаются |

### Адреса самого сервиса

| Env | Кит | Дефолт | Назначение |
|---|---|---|---|
| `HTTP_ADDR` | `httpkit` | `:8080` | bind HTTP-сервера (пустой host — все интерфейсы, dual-stack); `:0` → порт выбирает ОС (`Server.addr()`) |
| `GRPC_ADDR` | `grpckit` | `:9090` | bind gRPC-сервера (HTTP/2); `:0` → порт выбирает ОС |
| `GRPC_UDS_PATH` | `grpckit` | — (опц.) | дополнительный unix-сокет для co-located sidecar'ов |

### Внешние API и интеграции

| Env | Кит | Дефолт | Назначение |
|---|---|---|---|
| `HTTPCLIENT_BASE_URL` | `httpclient` | — (опц.) | базовый URL upstream'а по умолчанию; именованные бэкенды — `HTTPCLIENT_BACKENDS` |
| `BOT_WEBHOOK_BASE_URL` | `tgbotkit` | — (обязателен в webhook-режиме) | внешняя `https`-база самого бота для `set_webhook` — публичный адрес сервиса, не upstream |

`outboxkit` **не** имеет своего эндпоинта — он co-located с pg-хостом: outbox
пишется в той же транзакции, что и доменные данные (атомарность), поэтому живёт
на том же Postgres, что `PG_DSN`.

## Observability

Киты инструментированы OTel: HTTP/gRPC/DB/bus/cache/storage-вызовы эмитят
метрики и спаны через **глобальные** OTel-провайдеры (`_internal/otelkit` поверх
`metrics.get_meter`/`trace.get_tracer`, scope `pylibinfra.<kit>`, версия scope —
версия дистрибутива), а `logkit` пишет структурный лог в stdout и, при
подключённом `telemetry`, в OTLP. Пока приложение не зарегистрирует
`MeterProvider`/`TracerProvider`, инструменты — **no-op** (записи молча
отбрасываются, kit не падает). Экспорт и бэкенд — ответственность
потребителя: либо готовый opt-in кит `telemetry` (OTLP HTTP-экспортёры всех
трёх сигналов, endpoint/Bearer из env), либо собственная проводка OTel SDK.

Конфиг каждого кита провайдит caller — `Provider` его не читает. Пример ниже
собирается целиком (граф проверяется тестом `tests/test_readme_example.py`):

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import (
    cachekit,
    clockkit,
    healthkit,
    httpkit,
    logkit,
    natskit,
    outboxkit,
    pgkit,
    telemetry,
    timerkit,
)
from pylibinfra.outboxkit import pgstore

# Конфиги китов читает приложение: new_config() валидирует ENV и валит старт на ошибке.
configs = Provider(scope=Scope.APP)
configs.provide(telemetry.new_config)  # OTEL_*
configs.provide(logkit.new_config)  # LOG_*
configs.provide(httpkit.new_config)  # HTTP_*
configs.provide(pgkit.new_pool_config)  # PG_*
configs.provide(pgkit.new_migrate_runner_config)  # PG_MIGRATIONS_PATH, PG_MIGRATE_TIMEOUT
configs.provide(natskit.new_config)  # NATS_*
configs.provide(cachekit.new_config)  # REDIS_*
configs.provide(outboxkit.new_config)  # OUTBOX_*
configs.provide(pgstore.new_config)  # OUTBOX_RETENTION_MONTHS, OUTBOX_REQUIRE_TX


class AppProvider(Provider):
    """Пробы для /ready, миграции и job'ы — собирает приложение."""

    scope = Scope.APP

    @provide
    def migrations(self) -> pgkit.MigrationSources:
        return (pgstore.MIGRATIONS,)  # + миграции приложения — PG_MIGRATIONS_PATH

    @provide
    def probes(self, db: pgkit.DB, conn: natskit.Conn, cache: cachekit.Cache) -> healthkit.Probes:
        return (pgkit.probe(db), natskit.probe(conn), cachekit.probe(cache))

    @provide
    def jobs(self, outbox: outboxkit.Outbox, store: pgstore.Store) -> timerkit.Jobs:
        return (outbox.job(), pgstore.partition_keeper_job(store))


container = make_async_container(
    configs,
    logkit.LogkitProvider(),  # log_messages_total{level}, JSON с trace_id в stdout + OTLP
    telemetry.TelemetryProvider(),  # OTLP-экспортёры; без него всё ниже — no-op
    clockkit.ClockProvider(),  # Clock для timerkit и китов с TTL
    httpkit.HttpProvider(),  # http_requests_total, http_request_duration_seconds, server-span
    pgkit.PgProvider(),  # pg_pool_*{role}, pg traces
    pgkit.PgMigrateProvider(),  # миграции MigrationSources под advisory-lock
    natskit.NatsProvider(),  # nats_messages_*, nats_handle_duration_seconds, consumer-span
    cachekit.CachekitProvider(),  # cache_commands_total, cache_command_duration_seconds
    pgstore.OutboxPgstoreProvider(),  # репозиторий outbox'а, партиции и retention
    outboxkit.OutboxProvider(),  # outbox_*{instance}
    timerkit.TimerkitProvider(),  # timer_job_runs_total, timer_job_duration_seconds, спан timerkit.job
    AppProvider(),  # последним: перекрывает пустые дефолты Probes / Jobs / MigrationSources
)
```

На старте приложение резолвит `telemetry.Providers` (`await telemetry.start(container)`),
`pgkit.Migrated`, `httpkit.Server` и `timerkit.Runner` — dishka ленив, и ресурс
поднимается в момент первого запроса.

Имена метрик — Prometheus-стиль `<kit_prefix>_<what>_<unit|total>`; label'ы —
конечные множества (route-pattern, хост, инстанс, результат), не пути и не id.
Гистограммы длительности — в секундах, с общими bucket-границами
`_internal/otelkit.DURATION_BUCKETS` (`0.005 … 30`); для операций, штатно идущих
минутами, есть вторая шкала `LONG_DURATION_BUCKETS` (`0.005 … 600`). Собственных
границ киты не заводят.

### Business-метрики (`biz_*`)

Свои метрики бизнес-уровня — через `bizmetric` (форсит конвенцию
`biz_<domain>_<event>_total|_seconds|<gauge>`, исключение при нарушении):

```python
from pylibinfra.bizmetric import counter

logins = counter(meter, "auth", "login_attempts")  # → biz_auth_login_attempts_total
logins.add(1, {"result": "ok"})
```

### Что эмитит каждый пакет

Точный перечень сигналов кита (метрики — имена, типы, labels; спаны) живёт в его
README § Observability — у сложных китов в `docs/observability.md`. Сводно:

| Пакет | Метрики | Спаны |
|---|---|---|
| `logkit` | `log_messages_total{level}` | — |
| `telemetry` | `system.*`, `process.*`, `process.runtime.cpython.*`, `cpython.gc.*` (SystemMetricsInstrumentor) | — |
| `httpkit` | `http_requests_total{method,path,status}`, `http_request_duration_seconds`, `http_panics_total{path}`, `http_max_body_rejections_total{path}`, `http_rate_limited_total`, `http_idempotency_total{result}` | server span `METHOD /route/{pattern}` |
| `httpclient` | `http_client_request_duration_seconds{host,method,status,mode}`, `http_client_retry_attempts_total{host}`, `http_client_breaker_transitions_total{host,from,to}`, `http_client_breaker_open{host}` | `httpclient.Do <METHOD>`, `httpclient.Stream <METHOD>` + спаны попыток |
| `grpckit` | `grpc_server_requests_total{grpc_method,grpc_code}`, `grpc_server_request_duration_seconds{grpc_method,grpc_code}`, `grpc_server_panics_total{grpc_method}` | server span (otel-grpc) |
| `pgkit` | `pg_pool_{acquired,idle,total,max,min}_conns{role}` | per-query span (otel-asyncpg) |
| `storagekit` | `storage_ops_total{op,result}`, `storage_op_duration_seconds{op}` | — |
| `natskit` | `nats_messages_{published,consumed}_total`, `nats_{publish,handle}_duration_seconds`, `nats_inflight_messages` — все с label `instance` (+`transport=jetstream`) | producer `natskit.publish <шаблон>`, consumer `natskit.handle <фильтр>` |
| `outboxkit` | `outbox_events_{inserted,processed,dead}_total`, `outbox_revived_total`, `outbox_batch_{size,duration_seconds}`, `outbox_publish_duration_seconds`, `outbox_{lag_seconds,pending_count}` + `outbox_backlog_errors_total` (через `BacklogReporter`) — label `instance` | — (`timerkit.job` + `natskit.publish`) |
| `cachekit` | `cache_commands_total{cmd,result}`, `cache_command_duration_seconds{cmd}` | `redis <cmd>` (client) |
| `authnkit` | `authn_jwt_verifications_total{result}`, `authn_totp_verifications_total{result}`, `authn_password_{hash,verify}_duration_seconds` | — |
| `auditkit` | `audit_log_writes_total{result}`, `audit_log_write_duration_seconds{result}` | — |
| `bruteforcekit` | `bruteforce_checks_total{result}`, `bruteforce_locks_total`, `bruteforce_cleanup_deleted_total` | — |
| `timerkit` | `timer_job_runs_total{job,result}`, `timer_job_duration_seconds{job}` | `timerkit.job <name>` (root) |
| `tgbotkit` | `tg_updates_total{type,result}`, `tg_update_duration_seconds{type,result}`, `tg_update_panics_total{type}`, `tg_updates_deduped_total{type}`, `tg_updates_throttled_total{type}`, `tg_redis_errors_total{op}`, `tg_payments_total{stage,result}` | `tg.update` (в webhook-режиме дочерний к server-спану httpkit) |
| `bizmetric` | `biz_<domain>_<event>_total\|_seconds\|<gauge>` | — |
| `flagkit`, `config`, `clockkit`, `idkit`, `healthkit` | — | — |

## Стабильность API

Публичные конфиг-классы китов (их поля и `validation_alias`), экспортируемые
типы и сигнатуры — **part of public API**: `pgkit.PoolConfig`,
`pgkit.ReplicaConfig`, `pgkit.MigrateRunnerConfig`, `storagekit.Config`,
`httpkit.Config` (+ `CORSConfig`, `RateLimitConfig`, `MaxBodyConfig`,
`SecurityHeadersConfig`, `IdempotencyConfig`, `SharedSecretConfig`),
`grpckit.Config`, `natskit.Config` (+ `BackpressureConfig`, `JetStreamConfig`),
`httpclient.Config` (+ `BreakerConfig`, `BackendsConfig`, `BackendConfig`,
`BackendBreakerConfig`), `cachekit.Config`, `flagkit.Config`, `telemetry.Config`,
`logkit.Config` (+ `RedactConfig`, `ServiceConfig`), `authnkit.Argon2idParams`
(+ `JWTConfig`, `TOTPConfig`), `outboxkit.Config`, `outboxkit.pgstore.Config`,
`bruteforcekit.Config`, `tgbotkit.Config` (+ `I18nConfig`). Полный перечень полей
— в README каждого кита § Конфигурация; `make envdoc-check` сверяет alias'ы в
коде с документацией и с корневой env-таблицей.

Совместимость **между minor-версиями не гарантируется**: пока библиотека идёт
вместе со своими потребителями внутри stit, breaking-изменения выпускаются в
minor-релизе. Каждое такое изменение помечается `!` в Conventional Commit и
перечисляется в аннотации тега вместе с шагом миграции.

## Версионирование

Семвер по форме (`vX.Y.Z`, поле `version` в `pyproject.toml` + git-тег), политика:

- **minor** — новая функциональность и breaking-изменения; каждое breaking
  названо в аннотации тега (`Breaking` по китам: что изменилось → как мигрировать);
- **patch** — фиксы без изменения публичных сигнатур, дефолтов, имён метрик и
  env-alias'ов;
- **major** — резервируется на случай, когда потребители перестанут обновляться
  синхронно с библиотекой.

Релиз — skill `/release`: `make gate-clean` → bump версии → аннотированный тег с
release notes (`Breaking` / `Поведение` / `Изменения` / `Гейт`) → push. В проектах
после релиза:
```bash
uv add "pylibinfra @ git+https://git.stit.tech/stit-core/pylibinfra.git@vX.Y.Z" && uv sync
```

## Гейт

```bash
make sync         # uv sync --frozen
make hooks        # core.hooksPath=.githooks: pre-push гоняет make check
make check        # fmt-check + lint (ruff) + typecheck (mypy strict) + unit-тесты + envdoc-check
make test-full    # + testcontainers (Postgres/NATS/Redis/MinIO), нужен Docker
make gate-clean   # make check в чистом worktree от HEAD — перед тегом
```

Дополнительно: `make coverage` (покрытие полного прогона), `make vuln`
(`pip-audit` по графу зависимостей; требует сети).

## Локальная разработка с правкой pylibinfra

Правите `pylibinfra` параллельно с продуктом — временный editable-install в
проекте (**не коммитить**; перед мержем убрать и поднять версию через git-тег):
```bash
uv add --editable ../pylibinfra
```

## Часть экосистемы stit

Одна из открытых библиотек stit (Apache-2.0). Карта всех шаблонов и точка входа —
**[витрина stit](https://git.stit.tech/stit-core/stit-public)**.

## Лицензия

**Apache-2.0** — см. [`LICENSE`](LICENSE).
