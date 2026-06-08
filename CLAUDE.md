# pylibinfra — гайд для агентов

Набор инфраструктурных Python-библиотек («кит'ов») для сервисов stit:
observability, auth, БД, messaging, кэш, аудит и т.д. Идейный и структурный аналог
`golibinfra` для Python-стека. Не сервис и не stack — пакеты, которые импортирует
бизнес-репо (`uv add "pylibinfra @ git+…@vX.Y.Z"`).

Этот файл — **ядро (always-on) + роутер**. Полная human-доку — `README.md`
(использование, observability, стабильность API, версионирование). Контекстные
правила — `.claude/rules/` (path-scoped). Процедуры — `.claude/skills/`.

## Точка входа

- **Добавить новый kit** — skill `/add-kit` (scaffold пакета по конвенциям:
  `Provider`/dishka, observability-by-default, pydantic-settings Config, тесты,
  testkit-сабпакет, строка в README).
- **Выпустить версию** — skill `/release` (bump версии в `pyproject.toml` +
  `git tag vX.Y.Z` + push + гайд апдейта консьюмера).
- Точечная правка существующего kit'а — прямой Edit; scaffold не нужен.

## Команды (gate перед claim «готово»)

```bash
uv run ruff format --check .      # формат (gofumpt-аналог)
uv run ruff check .               # lint (golangci-аналог)
uv run mypy src                   # типы (библиотечный код; тесты — pytest+ruff)
uv run pytest -m "not integration"  # unit (≈ go test -short)
uv run pytest                     # полные (testcontainers; нужен Docker)
```

Зелёные все = можно claim «готово».

## Карта пакетов

Полный список с эмитимыми сигналами — `README.md § Пакеты`. Кратко: `telemetry`
(OTel bootstrap), `logkit` (structlog + PII-redact + trace-id), `httpkit`
(FastAPI server + middleware), `httpclient` (httpx + retry + circuit breaker),
`pgkit` (asyncpg pool + otel), `natskit` (NATS/JetStream), `outboxkit` (outbox +
lag-reporter), `authnkit` (JWT/Argon2id/TOTP/RBAC), `cachekit` (Redis +
idempotency), `bizmetric` (business-метрики), `auditkit`, `bruteforcekit`,
`flagkit`, `clockkit`, `timerkit`, `config` (env + Vault), `testkit`
(testcontainers helpers).

## Инварианты, на которые завязан корп-стек

Полно — `README.md § Minimum viable observability`. Чек-лист:

1. **`service.name` / `service.version` / `deployment.environment.name`** —
   выставляет приложение через `telemetry.Config`. Без них дашборды Apps/* теряют
   `$service`/`$env`.
2. **`tenant` / `service.namespace`** — приложение **не** выставляет (кроме
   local-dev через `OTEL_TENANT`); их ставит корп `otel-collector`. Выставишь сам —
   будет молча перезатёрто.
3. **`biz_<domain>_<event>_total|_seconds|<gauge>`** — единственная конвенция имён
   business-метрик; enforce'ится `bizmetric.counter/histogram/gauge` (raise при нарушении).

Правка `telemetry/`, `logkit/`, `bizmetric/` или resource-attrs init-флоу — breaking
для всех сервисов; сверяйся с корп obs-контрактом перед изменением инвариантов.

## Стиль (инварианты)

- **ruff format + ruff check** (`pyproject.toml`), **mypy strict**. Полная
  типизация, `py.typed` экспортится.
- **dishka-first.** Каждый kit отдаёт `Provider` (`<Kit>Provider`); консьюмер
  собирает контейнер из них через `make_async_container`. Прямой `New*()`/фабрики
  допустимы, но менее идиоматичны.
- **async-first.** Весь I/O — asyncio (asyncpg / nats-py / redis.asyncio / httpx /
  FastAPI). Проброс tx/conn — через `contextvars` (аналог `context.Context`).
- **pydantic-settings Config = public API.** Имена полей и их `validation_alias`
  (env-имена) менять = breaking → новый major.
- **Asymmetry note.** Provider kit'а **не** provide'ит свой Config — caller
  регистрирует сам (типично через App-Provider). Дешёвый mode — `new_config()`.
- **Observability by default.** Новый kit эмитит метрики/спаны через otel-python
  SDK, читая resource attrs из telemetry (не хардкодить).
- **Минимум зависимостей.** Перед новым import — проверь stdlib + существующие deps;
  каждый новый import обсуждается с пользователем.
- **Комментарии — только WHY** (rule `.claude/rules/comments.md`).

## Git workflow

- **Conventional Commits**: `feat|fix|refactor|docs|test|chore` + `<type>(<scope>)?: <subject>`.
- Линейная история; версия — git-тег `vX.Y.Z` (pre-v1: minor может быть breaking).
- Коммит/пуш — гейт человека (без явной просьбы не коммитить).

## Границы

### Делать всегда
- Перед claim «готово» — ruff + mypy + pytest зелёные.
- Новый kit — через `/add-kit`; строка в `README.md § Пакеты` обязательна.
- Тронул эмитимые метрики/labels/span-attrs — обнови `README.md § Что эмитит каждый пакет`.

### Сначала спросить
- Менять public Config-структуры (имена полей, env-alias'ы, типы) — breaking, нужен major.
- Менять `telemetry/`/`bizmetric/`/resource-attrs — затрагивает все сервисы.
- Добавлять новую зависимость в `pyproject.toml`.

### Не делать никогда
- Выставлять `tenant`/`service.namespace` дефолтом в `telemetry.Config`.
- `:latest` в примерах. `print()` / `logging.getLogger()` напрямую в production-коде —
  только `logkit.Logger`.
- Коммитить product-specific логику — pylibinfra общая lib; доменное живёт в сервис-репо.

## Forward-only

Любой текст в репо описывает **только текущее состояние** — не «было/стало», без дат
прошлого. История — в `git log` + Conventional Commits. Правило — `.claude/rules/forward-only.md`.
