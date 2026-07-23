# pylibinfra — гайд для агентов

Набор инфраструктурных Python-библиотек («кит'ов») для сервисов stit:
auth, БД, messaging, кэш, аудит и т.д. Идейный и структурный аналог
`golibinfra` для Python-стека. Не сервис и не stack — пакеты, которые импортирует
бизнес-репо (`uv add "pylibinfra @ git+…@vX.Y.Z"`).

Этот файл — **ядро (always-on) + роутер**. Полная human-доку — `README.md`
(использование, наблюдаемость, стабильность API, версионирование). Контекстные
правила — `.claude/rules/` (path-scoped). Процедуры — `.claude/skills/`.

## Точка входа

- **Добавить новый kit** — skill `/add-kit` (scaffold пакета по конвенциям:
  `Provider`/dishka, pydantic-settings Config, тесты,
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

Полный список с эмитимыми сигналами — `README.md § Пакеты`. Кратко: `telemetry` (opt-in OTel bootstrap), `logkit`
(structlog + PII-redact, stdout/file), `httpkit`
(FastAPI server + middleware), `httpclient` (httpx + retry + circuit breaker),
`pgkit` (asyncpg pool), `natskit` (NATS/JetStream), `outboxkit` (outbox +
lag-reporter), `authnkit` (JWT/Argon2id/TOTP/RBAC), `cachekit` (Redis +
idempotency), `bizmetric` (business-метрики), `auditkit`, `bruteforcekit`,
`flagkit`, `clockkit`, `timerkit`, `config` (env), `testkit`
(testcontainers helpers).

## Инварианты

Полно — `README.md § Наблюдаемость`. Чек-лист:

1. **`biz_<domain>_<event>_total|_seconds|<gauge>`** — единственная конвенция имён
   business-метрик; enforce'ится `bizmetric.counter/histogram/gauge` (raise при нарушении).
2. Киты инструментированы OpenTelemetry-API через глобальный provider; без
   сконфигурированного provider'а — **no-op**. Экспортёр/бэкенд и resource attrs
   (`service.name`/`service.version`/`deployment.environment.name`) задаёт консьюмер
   в своём OTel-SDK — не хардкодить в китах.

Правка `logkit/`, `bizmetric/` или эмитимых метрик/labels/span-attrs — breaking для
консьюмеров; сверяйся перед изменением инвариантов.

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
- **Observability opt-in.** Новый kit эмитит метрики/спаны через otel-python API
  (глобальный provider; no-op без него), не хардкодит resource attrs — их задаёт
  OTel-SDK консьюмера.
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
- Менять `bizmetric/` или эмитимые метрики/labels/span-attrs — затрагивает консьюмеров.
- Добавлять новую зависимость в `pyproject.toml`.

### Не делать никогда
- `:latest` в примерах. `print()` / `logging.getLogger()` напрямую в production-коде —
  только `logkit.Logger`.
- Коммитить product-specific логику — pylibinfra общая lib; доменное живёт в сервис-репо.

## Forward-only

Любой текст в репо описывает **только текущее состояние** — не «было/стало», без дат
прошлого. История — в `git log` + Conventional Commits. Правило — `.claude/rules/forward-only.md`.
