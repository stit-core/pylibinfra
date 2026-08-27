<div align="center">

# pylibinfra

### Инфраструктурные Python-kit'ы для сервисов stit

Набор переиспользуемых Python-библиотек для сервисов экосистемы stit:
auth, БД, messaging, кэш, аудит, storage. Каждый kit — `dishka.Provider`,
подключается независимо. Импортируется как Python-пакет из
бизнес-репозиториев. Идейный и структурный аналог
[`golibinfra`](https://git.stit.tech/stit-core/golibinfra) для Python-стека.

[![License](https://img.shields.io/badge/license-Apache--2.0-2563eb)](LICENSE)
[![Часть stit](https://img.shields.io/badge/stit-открытые_шаблоны-6366f1)](https://git.stit.tech/stit-core/stit-public)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB)](#использование-в-проекте)
[![Kits](https://img.shields.io/badge/kits-20-0ea5e9)](#пакеты)

</div>

```python
from pylibinfra.pgkit import PgProvider
from pylibinfra.httpkit import HttpProvider
from pylibinfra import config
from pylibinfra import testkit
```

## Где это в экосистеме

`pylibinfra` — общая Python-lib экосистемы stit, симметричная `golibinfra` для
Go-сервисов. Карта всех репо — на
[витрине stit](https://git.stit.tech/stit-core/stit-public).

## Доступ к репозиторию

Репо лежит на собственной Gitea (`git.stit.tech`), не на публичном PyPI. Установка
из git напрямую через uv/pip:

```bash
# uv (рекомендуется)
uv add "pylibinfra @ git+https://git.stit.tech/stit-core/pylibinfra.git@v0.1.0"

# pip
pip install "pylibinfra @ git+https://git.stit.tech/stit-core/pylibinfra.git@v0.1.0"
```

Для авторизованного доступа — git-аутентификация SSH-ключом или токеном (как в
`golibinfra`). Приватный индекс для CI можно поднять через Gitea PyPI-registry.

## Использование в проекте

```toml
# pyproject.toml
[project]
dependencies = [
  "pylibinfra @ git+https://git.stit.tech/stit-core/pylibinfra.git@v0.1.0",
]
```

`uv lock && uv sync` подтянет.

## Принципы

- **Никакой продуктовой специфики.** Если модуль знает про конкретные роли, домены,
  имена сервисов — он не здесь.
- **Без скрытых зависимостей.** Каждый пакет подключается независимо.
- **dishka-first.** Каждый пакет экспортирует `Provider` для проводки в DI-контейнер.
- **async-first.** Весь I/O — на asyncio (asyncpg / nats-py / redis.asyncio / httpx / FastAPI).

## Пакеты

Per-kit `README.md` в каталогах китов осознанно **не заводим**: единый источник
правды по каждому киту — эта таблица (ответственность) + § Стабильность API
(public-конфиги) + § Что эмитит каждый пакет (телеметрия).

| Пакет | Ответственность |
|---|---|
| `pgkit` | `DB`-фасад поверх asyncpg, `Transactor` (через `contextvars`), migrate-runner (yoyo), маппинг pg-ошибок, `DBProbe` health-probe; `ReadWriteDB` для read-replica (`PG_REPLICA_DSN`) |
| `storagekit` | S3/object storage поверх aioboto3: `Storage` (`put`/`delete`), `URLBuilder`, in-memory адаптер для тестов, `s3`-подпакет; `Config` + `new_key` (uuidv7 + ext) |
| `httpkit` | FastAPI bootstrap; middleware: RequestID, Recovery, AccessLog, OTel, CORS, RateLimit, MaxBody, Metrics, Auth, **SecurityHeaders**, **Idempotency**; `APIError`, `map_err`, `map_sentinel`; `dishka_lifespan`, `shared_secret_guard` (для вебхуков) |
| `natskit` | Pub/sub поверх NATS: `Bus` (`subscribe`/`subscribe_queue`), `publish_json`/`subscribe_json`/`subscribe_queue_json`, опц. JetStream `StreamBus` (батч-async публикация + дедуп по `Nats-Msg-Id`; poison-обработка `NATS_JS_MAX_DELIVER`→DLQ/term, NAK с backoff), subject/headers сообщения через `subject_from_context()`/`headers_from_context()`; panic recovery + backpressure (`NATS_MAX_IN_FLIGHT`) |
| `tgbotkit` | aiogram-bootstrap (как httpkit для FastAPI): `BotProvider`, polling/webhook `BotRunner`, outer-цепочка (request-id/dedup/throttle/RED+span/recovery), error-UX, `setup_dialogs`; точки расширения `RouterBundle`/`CommandBundle`/`NotifierBundle`/`ErrorUX`; подсистемы i18n (fluent)/notifications (JetStream; plain-фолбэк, если Telegram отверг разметку)/payments (XTR)/webapp (initData); `escape_markdown_v2`/`_code`/`_link` — вставка динамики в MarkdownV2 без потери сообщения. **extra `[tgbotkit]`** (aiogram, aiogram-dialog, fluent.runtime) |
| `grpckit` | grpc.aio-сервер (как httpkit для FastAPI): `GrpcProvider`, `Server` (bind UDS и/или TCP, graceful-drain), интерсепторы OTel-трейс + observability (метрики/access-log/recovery), стандартный `grpc.health.v1`; точки расширения `ServicerBundle` (регистраторы servicer'ов консьюмера) / `ProbeBundle` (readiness). **extra `[grpckit]`** (grpcio, grpcio-health-checking, opentelemetry-instrumentation-grpc) |
| `httpclient` | HTTP-клиент поверх httpx с retry (tenacity), circuit breaker (purgatory), OTel и автопередачей `X-Request-ID`; `ApiClient` — JSON-слой: декод в wire-модель (`model=`), таксономия typed-ошибок (`NotFound`/`Conflict`/`Auth`/`Forbidden`/`InvalidRequest`/`RateLimited`/`Contract`/`Unavailable` + status/retry_after/body-сниппет), ретраи мутаций только с `Idempotency-Key`, именованные бэкенды (`ApiClients` из `BackendConfig`: обязательный `base_url`, статические `headers`, breaker-имя = ключ бэкенда) |
| `cachekit` | Redis-обёртка: `Cache`, `get_json`/`set_json`, `invalidate_by_tag`; `IdempotencyStore` для `httpkit` |
| `flagkit` | Feature flags из ENV (`FEATURE_FLAGS=foo=true,bar=baz`); `Store.bool/str` |
| `timerkit` | Periodic jobs через asyncio: регистрация задач с интервалом, lifecycle-aware |
| `telemetry` | Opt-in bootstrap OTel SDK: tracer/meter/logger providers + OTLP HTTP-экспортёры (endpoint/Bearer из env); resource-атрибуты `service.*`, опц. `tenant`; system-метрики |
| `logkit` | `Logger` поверх structlog; JSON → stdout/file (`LOG_STDOUT`); redact (PII-фильтры) |
| `authnkit` | `Hasher` (Argon2id); `JWTConfig` + sign/verify; `TOTPGenerator` (RFC 6238); `Permission`/`RolePermissions`/`has` для RBAC |
| `bizmetric` | Helper для бизнес-метрик: `counter/histogram/gauge`, форсит конвенцию `biz_<domain>_<event>_*` (raise при нарушении) |
| `outboxkit` | Transactional outbox: `Worker` публикует в JetStream через `Transport` (`OUTBOX_PUBLISH_MODE` batch\|ordered), `Bus`, multi-instance `Outbox`, core+pgstore split; poison→dead (`OUTBOX_MAX_ATTEMPTS`), retention (`OUTBOX_RETENTION_MONTHS`), `BacklogReporter` для `outbox_lag_seconds`/`outbox_pending_count` |
| `auditkit` | Audit-log: `Entry`/`Recorder`, core+pgstore split |
| `bruteforcekit` | Brute-force guard: per-subject + per-IP счётчики, `LockState`, core+pgstore split |
| `clockkit` | `Clock` + test-fakes (`Fixed`, `Tickable`) для детерминированных временных тестов |
| `config` | Загрузчик: env (pydantic-settings), композиция `Source` для приложения |
| `testkit` | testcontainers: `start_postgres`, `start_nats`, `start_redis`; httptest helpers; per-kit subpackages |

## Per-concern endpoints (multi-host)

Каждый кит читает свой эндпоинт из ENV — **разное значение = разный хост**. Это
«шов» для split-топологии: storage / Postgres / NATS / Redis можно
развести по отдельным серверам, **не трогая код**.

| Env | Кит | Назначение |
|---|---|---|
| `PG_DSN` | `pgkit` | Postgres (primary) |
| `PG_REPLICA_DSN` | `pgkit` | Postgres read-replica (опц.; `ReadWriteDB`) |
| `STORAGE_S3_ENDPOINT` | `storagekit` | S3 / MinIO object storage |
| `NATS_URL` | `natskit` | NATS / JetStream |
| `REDIS_URL` | `cachekit` | Redis (cache + idempotency store) |

`outboxkit` **не** имеет своего эндпоинта — он co-located с pg-хостом: outbox
пишется в той же транзакции, что и доменные данные (атомарность).

## Наблюдаемость (opt-in)

Киты инструментированы **OpenTelemetry-API** (спаны + метрики) через глобальный
провайдер otel-python. Без сконфигурированного OTel-provider'а инструментация —
**no-op**: экспортёр и бэкенд подключает консьюмер сам — готовым opt-in
китом `telemetry` (OTLP HTTP-экспортёры, endpoint/Bearer из env) или
собственной проводкой OTel SDK. `logkit` пишет
structlog-JSON в stdout/file независимо от OTel.

Обязательные resource attrs (`service.name`, `service.version`,
`deployment.environment.name`) и настройку экспортёра caller задаёт в своём
OTel-SDK — pylibinfra их не хардкодит.

### Business-метрики (`biz_*`)

```python
from pylibinfra.bizmetric import counter

logins = counter(meter, "auth", "login_attempts")  # → biz_auth_login_attempts_total
logins.add(1, {"result": "ok"})
```

`bizmetric` форсит конвенцию имён `biz_<domain>_<event>_*` — helper `raise`'ит при
нарушении. `meter` caller получает из своего OTel-SDK.

### Что эмитит каждый пакет (короткий reference)

| Пакет | Метрики | Спаны |
|---|---|---|
| `logkit` | `log_messages_total{level}` | — |
| `httpkit` | `http_requests_total`, `http_request_duration_seconds`, `http_panics_total`, `http_max_body_rejections_total`, `http_idempotency_total` | server span (otel-asgi) |
| `httpclient` | `http_client_request_duration_seconds`, `http_client_breaker_*`, `http_client_retry_attempts_total` | client span |
| `pgkit` | `db_pool_*` | per-query span (otel-asyncpg) |
| `storagekit` | — | — |
| `natskit` | `nats_messages_{published,consumed}_total` (consumed status: `ok\|handler_error\|panic\|dead`), `nats_{publish,handle}_duration_seconds`, `nats_inflight_messages` | producer + consumer spans (trace-propagation) |
| `tgbotkit` | `tg_updates_total{type,result}`, `tg_update_duration_seconds`, `tg_update_panics_total`, `tg_updates_deduped_total`, `tg_payments_total{stage,result}` | per-update span `tg.update {type}` (сшивается с webhook-span httpkit) |
| `grpckit` | `grpc_server_requests_total{grpc_method,grpc_code}`, `grpc_server_request_duration_seconds`, `grpc_server_panics_total` | server span (otel-grpc-instrumentation) |
| `outboxkit` | `outbox_events_{inserted,processed,dead}_total`, `outbox_batch_*`, `outbox_publish_duration_seconds`, `outbox_lag_seconds`/`outbox_pending_count` (opt-in) | worker span |
| `authnkit` | `jwt_verifications_total`, `password_*_duration_seconds`, `totp_verifications_total` | — |
| `bizmetric` | `biz_<domain>_<event>_total\|_seconds\|<gauge>` | — |

### Anti-patterns

- **Не плодить biz-метрики обходя `bizmetric.counter`** — helper форсит конвенцию.
- **Не хардкодить resource attrs в китах** — их выставляет OTel-SDK консьюмера.

## Стабильность API

Публичные `pydantic-settings`-структуры конфигов (`pgkit.PoolConfig`,
`pgkit.ReplicaConfig`, `storagekit.Config`, `httpkit.Config`,
`httpkit.SecurityHeadersConfig`, `httpkit.IdempotencyConfig`,
`logkit.Config`, `logkit.RedactConfig`, `authnkit.Argon2idParams`,
`cachekit.Config`, `flagkit.Config`, `natskit.Config`, `httpclient.BreakerConfig`,
`httpclient.BackendsConfig`, `httpclient.BackendConfig`,
`httpkit.SharedSecretConfig`, `tgbotkit.Config`, `tgbotkit.I18nConfig`,
`grpckit.Config`) — **part of public API**. Менять имена полей или их
env-alias'ы = breaking change, идёт мажорным апгрейдом.

## Версионирование

Семвер, теги `v0.x.y`. До `v1.0.0` API нестабилен — breaking changes допустимы в
minor. Релиз — skill `/release` (bump версии в `pyproject.toml` + `git tag vX.Y.Z`).

## Локальная разработка

```bash
uv sync                              # установить deps + dev-инструменты
uv run ruff format . && uv run ruff check .   # format + lint
uv run mypy src                      # типы
uv run pytest -m "not integration"   # быстрые тесты (≈ go test -short)
uv run pytest                        # полные (testcontainers; нужен Docker)
```

Правите `pylibinfra` параллельно с продуктом — временный editable-install в проекте
(`uv add --editable ../pylibinfra`); перед мержем убрать и поднять версию через git-тег.

## Часть экосистемы stit

Одна из открытых библиотек stit (Apache-2.0). Карта всех шаблонов и точка входа —
**[витрина stit](https://git.stit.tech/stit-core/stit-public)**.

## Лицензия

**Apache-2.0** — см. [`LICENSE`](LICENSE).
