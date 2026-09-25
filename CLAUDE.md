# pylibinfra — гайд для агентов

Набор инфраструктурных Python-библиотек («кит'ов») для сервисов stit: observability,
auth, БД, messaging, кэш, аудит, storage, Telegram-бот и т.д. Идейный и структурный
аналог `golibinfra` для Python-стека: те же имена китов, те же ENV, те же метрики.
Не сервис и не stack — пакеты, которые импортирует бизнес-репо
(`uv add "pylibinfra @ git+…@vX.Y.Z"`).

Этот файл — **ядро (always-on) + роутер**. Полная human-доку — `README.md`
(использование, observability, стабильность API, версионирование) и `README.md`
каждого кита рядом с кодом (`src/pylibinfra/<kit>/README.md`, у сложных ещё `docs/`).
Контекстные правила — `.claude/rules/` (path-scoped). Процедуры — `.claude/skills/`.
Планы — `.claude/plans/` (skill `/plan`).

## Место в экосистеме

Экосистема stit — 7 шаблонов `stit-core/*`, которые форкаются в репо клиентской орги
`<org>/*`, и две библиотеки: `golibinfra` (Go) и эта. pylibinfra не форкается:
`telegram-bot-template` и его форки `<org>/<bot>` тянут пакет из git по тегу
(`[tool.uv.sources]`, `rev = "vX.Y.Z"`). Отсюда уходит только тег (`/release`): ревизию и
миграцию по аннотации тега потребитель делает своей обвязкой, выкатку образа бота —
`<org>/infra` (`make bump`).

Сюда приходят прямым заходом — сопровождать библиотеку — или с задачей из бота: новую
инфра-механику (middleware, runner, helper кита) бот оформляет через `/infra-handoff` как PR
в `pylibinfra`. Работа здесь идёт обвязкой этого репо, гейт — `make check`. Канон карты репо
и операционного флоу — `docs/reference/ecosystem.md` репо `project-infra-template-compose`
(в workspace — `../project-infra-template-compose/docs/reference/ecosystem.md`).

## Точка входа

- **Добавить новый kit** — skill `/add-kit` (scaffold пакета по конвенциям:
  dishka `Provider`, pydantic-settings Config с fail-fast валидацией, observability через
  `_internal/otelkit`, healthkit-проба, тесты, testkit-сабпакет, README кита, строка в
  корневом README и в карте ниже).
- **Выпустить версию** — skill `/release` (`make gate-clean` → bump `pyproject.toml` →
  аннотированный тег `vX.Y.Z` с release notes → push → гайд апдейта консьюмера).
- **Содержательное изменение** (breaking в ките, миграция, работа на несколько коммитов) —
  через план: skill `/plan` (`.claude/plans/<slug>.md`, бэклог — `.claude/plans/backlog.md`).
- Точечная правка существующего kit'а — прямой Edit; scaffold не нужен.

## Команды (gate перед claim «готово»)

```bash
make check        # fmt-check + lint (ruff) + typecheck (mypy strict) + unit-тесты + envdoc-check
make test-full    # + testcontainers (Postgres/NATS/Redis/MinIO), нужен Docker
make gate-clean   # make check в чистом worktree от HEAD — перед тегом
make hooks        # core.hooksPath=.githooks: pre-push гоняет make check
make coverage     # покрытие полного прогона; ещё есть vuln (pip-audit, нужна сеть)
```

Зелёный `make check` = можно claim «готово». Перед тегом — `make gate-clean`:
он ловит файлы, которые есть локально, но не в git.

## Карта пакетов

Полный список — `README.md § Пакеты`. Назначение, конфиг и эмитимые сигналы
каждого кита — в его `README.md` рядом с кодом (у сложных китов ещё `docs/`);
это единый источник правды по киту. Кратко:

- **Фундамент:** `logkit` (`Logger` + PII-redact + trace/request-id, JSON/text в stdout +
  OTel log-bridge), `telemetry` (opt-in OTel bootstrap: провайдеры + OTLP-экспортёры;
  требует в контейнере `logkit.Config`), `config` (композиция источников для
  App-конфига), `clockkit` (`now`/`since`/`sleep` + фейки), `timerkit` (periodic jobs;
  кортеж `timerkit.Jobs` собирает консьюмер), `flagkit`, `idkit` (UUID v7 +
  `validate`/`is_v7`), `bizmetric` (конвенция `biz_*`), `healthkit` (`Probe(name, check)` +
  `with_timeout`; кортеж `healthkit.Probes` для `/ready` и health-loop gRPC).
- **Транспорт:** `httpkit` (FastAPI/uvicorn server + middleware), `httpclient` (httpx +
  retry + breaker на хост + redirect-политика + SSE + `ApiClient`), `grpckit`
  (grpc.aio-сервер; extra `[grpckit]`), `tgbotkit` (aiogram-bootstrap; extra `[tgbotkit]`).
- **Данные и messaging:** `pgkit` (asyncpg pool + миграции yoyo с версией на источник +
  Transactor), `natskit` (NATS/JetStream), `outboxkit` (transactional outbox), `cachekit`
  (Redis ≥ 7 + idempotency store), `storagekit` (S3/MinIO через aioboto3), `auditkit`,
  `bruteforcekit`.
- **Auth:** `authnkit` (Argon2id/HS256 JWT/TOTP/RBAC).
- **Тесты:** `testkit` (testcontainers: `start_postgres/start_nats/start_redis/start_minio`,
  `require_docker`) + сабпакеты `testkit/<name>test` (`audittest`, `authntest`, `bottest`,
  `bruteforcetest`, `grpctest`, `handlertest`, `logtest`, `natstest`, `outboxtest`,
  `pgtest`, `storagetest`) — внутри китов test-хелперов нет.
- **Служебное:** `_internal/otelkit` (scope `pylibinfra.<kit>`, версия инструментации,
  `meter`/`tracer`, две шкалы бакетов — `DURATION_BUCKETS` и `LONG_DURATION_BUCKETS`,
  `duration_histogram`), `_internal/requestid` (contextvar request-id, общий для
  транспортов и клиентов), `_internal/configkit` (`KitSettings`, `settings_config`,
  `ConfigError`, `build`). Наружу не экспортируются.

## Observability-инварианты

Киты инструментированы OTel и эмитят метрики/спаны через **глобальные** провайдеры
(`otelkit.meter`/`otelkit.tracer` поверх `metrics.get_meter`/`trace.get_tracer`); без
зарегистрированного `MeterProvider`/`TracerProvider` инструменты — no-op, kit не
падает. Экспорт и бэкенд — `telemetry` или собственная проводка OTel SDK на
стороне потребителя. Подробно — `README.md § Observability`.

- Имена метрик — Prometheus-стиль `<kit_prefix>_<what>_<unit|total>`; label'ы —
  конечные множества (route-pattern, а не путь; хост, а не URL).
- **`biz_<domain>_<event>_total|_seconds|<gauge>`** — единственная конвенция имён
  business-метрик; enforce'ится `bizmetric.counter/histogram/gauge/up_down_counter`
  (исключение при нарушении).
- Гистограммы длительности — в секундах, через `otelkit.duration_histogram` с общими
  границами `DURATION_BUCKETS`; для операций, штатно идущих минутами, —
  `long=True` (`LONG_DURATION_BUCKETS`). Своих bucket-границ киты не заводят.
- Health-проба кита — именованная `healthkit.Probe`; кит отдаёт фабрику
  `<kit>.probe(...)`, консьюмер складывает пробы в кортеж `healthkit.Probes`, который
  читают `/ready` в `httpkit` и health-loop `grpckit`.
- DI-группы (аналог fx value-groups) — кортеж-типы `<kit>.<Things>`
  (`healthkit.Probes`, `timerkit.Jobs`, `pgkit.MigrationSources`, `grpckit.Services`, …);
  их собирает App-Provider консьюмера, киты дают фабрики.

## Стиль (инварианты)

- **ruff format + ruff check** (`pyproject.toml`), **mypy strict**. Полная типизация,
  `py.typed` экспортится.
- **dishka-first.** Каждый kit отдаёт `Provider` (`<Kit>Provider`, `Scope.APP`); консьюмер
  собирает контейнер из них через `make_async_container`. Lifecycle — async-generator-
  `@provide` (fail-fast подключение до `yield`, закрытие после). Прямые фабрики (`new_*`)
  допустимы, но менее идиоматичны.
- **Provider кита не даёт дефолтов для чужих общих типов** (`logkit.Logger`, `clockkit.Clock`):
  их provide'ят владельцы (`LogkitProvider`, `ClockProvider`), иначе порядок регистрации
  молча подменял бы реальный логгер/часы дефолтом. Свои опциональные зависимости кит
  выражает override-ключами `Optional*`/кортежами (`healthkit.Probes`, `timerkit.Jobs`),
  дефолт которых консьюмер перекрывает провайдером, зарегистрированным после (в dishka
  побеждает последний).
- **Config провайдит caller.** Provider не читает ENV и не provide'ит `Config`:
  консьюмер регистрирует его сам (App-Provider) либо через `<kit>.new_config()`.
  Каждый конфиг наследует `configkit.KitSettings` (`settings_config()` + ENV только по
  alias'ам, без `.env`); кросс-полевые инварианты — `model_validator`; `new_config()`
  переводит ошибки в `<kit>.ConfigError` без значений полей, поэтому невалидный ENV
  валит старт, а не первый запрос.
  Zero-value `Config` либо безопасен и полезен, либо отвергается — «молча ломается» нельзя.
- **Имена полей и `validation_alias` — часть public API.** Менять = breaking: `!` в
  коммите + строка миграции в аннотации тега. Длительности — секунды (`float`),
  размеры — байты, секреты — `SecretStr`.
- **Docstring на каждом экспорте** (ruff `D100–D106` в гейте): одна строка, по делу;
  package-doc — 1–3 строки: что это и какие сигналы эмитит.
- **Язык:** тексты ошибок и логов — английские, классы исключений `<X>Error`
  с префиксом кита в сообщении (`"pgkit: …"`, у сабпакета — `"outboxkit/pgstore: …"`);
  комментарии и docstring'и — русские, как в окружающем коде.
- **async-first.** Весь I/O — asyncio; CPU-тяжёлое — `asyncio.to_thread`; проброс
  tx/request-id/subject — `contextvars`. Каждый исходящий вызов — с таймаутом.
- **Observability by default.** Новый kit эмитит метрики/спаны через
  `_internal/otelkit`; resource attrs и экспорт настраивает потребитель.
- **Минимум зависимостей.** Перед новым import — проверь stdlib + существующие deps;
  каждый новый import обсуждается с пользователем. Одна библиотека на задачу.
- **Комментарии внутри функций — только WHY** (rule `.claude/rules/comments.md`).

## Git workflow

- **Conventional Commits**: `feat|fix|refactor|docs|test|chore` + `<type>(<scope>)?: <subject>`;
  breaking — `!` и футер `BREAKING CHANGE:`.
- Линейная история; версия — поле `version` в `pyproject.toml` + тег `vX.Y.Z`.
  Совместимость между minor-версиями не гарантируется: breaking идёт **minor**'ом с `!` в
  коммите и строкой миграции в аннотации тега; patch — только фиксы без смены сигнатур,
  дефолтов, имён метрик и env-alias'ов. Аннотация тега — release notes: `Breaking` по
  китам (что изменилось → как мигрировать), `Поведение`, `Изменения`, `Гейт`.
- Коммит/пуш — гейт человека (без явной просьбы не коммитить).

## Границы

### Делать всегда
- Перед claim «готово» — `make check` зелёный; перед тегом — `make gate-clean`
  (§ Команды).
- Новый kit — через `/add-kit`: без собственного `README.md` кита (+ `docs/` у сложного)
  и строки-индекса в `README.md § Пакеты` потребитель его не найдёт.
- Тронул конфиг / эмитимые метрики / labels / span-attrs — обнови `README.md`
  (+ `docs/`) соответствующего кита (§ Конфигурация / § Observability) и, если
  переменная — endpoint, строку в `README.md § Per-concern endpoints`:
  `make envdoc-check` сверяет обе стороны и падает на расхождении.
- Каждый багфикс — с регрессионным тестом; у `Provider` кита — тест сборки контейнера
  с минимальным набором провайдеров.

### Сначала спросить пользователя
- Смена конвенции имён метрик (`bizmetric`, `biz_*`) — на неё опираются все сервисы.
- Новая зависимость в `pyproject.toml` — она уезжает в граф каждого потребителя.
- Удаление кита или смена его границ — breaking для потребителей.

### Не делать
- `:latest` в примерах и тестах: тег плавает, и тест перестаёт быть воспроизводимым.
- `print()` / `logging.getLogger()` напрямую в production-коде: лог идёт только через
  `logkit.Logger`, иначе теряет redact, request-/trace-id и OTel-sink.
- Product-specific логика: pylibinfra — общая lib, доменное живёт в сервис-репо.
- Клиент без таймаута — зависший вызов держит задачу. `datetime.now()`/`asyncio.sleep()`
  в ките, чьё поведение зависит от времени, — только через `clockkit.Clock`, иначе это
  поведение не проверить фейковыми часами.

## Forward-only

Любой текст в репо описывает **только текущее состояние** — не «было/стало», без дат
прошлого. История — в `git log` + Conventional Commits + аннотации тегов. Правило —
`.claude/rules/forward-only.md`.
