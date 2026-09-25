---
name: add-kit
description: Завести в pylibinfra новый kit по конвенциям библиотеки — package-doc, dishka Provider, Config на configkit.KitSettings с fail-fast валидацией, observability через _internal/otelkit, healthkit-проба при внешней зависимости, тесты, testkit-сабпакет, README кита (+ docs/ у сложного) и строки в корневом README и карте CLAUDE.md. Используй, когда нужен новый переиспользуемый пакет — инфраструктурный примитив, клиент или helper для нескольких сервисов. Правка существующего кита — прямой Edit, без скилла.
---

# Skill: add-kit

Заводит новый переиспользуемый примитив как отдельный пакет-kit. pylibinfra —
общая lib: сюда едет только то, что не специфично для бизнес-домена (доменное
живёт в сервис-репо).

## Когда применять

- "Нужен переиспользуемый kit для X" / "добавь `<name>kit`" / "/add-kit".
- Примитив (generic util, middleware, клиент, helper) нужен ≥2 сервисам или
  концептуально инфраструктурный.

## Когда НЕ применять

- Правка существующего kit'а → прямой Edit.
- Логика, специфичная для одного бизнес-домена → она в сервис-репо, не сюда.

## Шаги

1. **Слаг.** Имя пакета — `<area>kit` (нижний регистр, без подчёркиваний):
   `cachekit`, `authnkit`. Спроси у пользователя назначение и публичный API, если
   неочевидно.
2. **Каталог пакета** `src/pylibinfra/<kit>/`:
   - `__init__.py` — package-doc (1–3 строки: что это и какие сигналы эмитит) +
     реэкспорт публичного API + `__all__`. Docstring на каждом публичном модуле,
     классе, функции и методе (ruff `D100–D106` в гейте).
   - `<kit>.py` (или несколько модулей) — реализация. Ошибки — классы `<X>Error`
     с английским текстом и префиксом кита (`"<kit>: …"`); базовый
     `ConfigError(configkit.ConfigError)` при наличии конфига.
   - `module.py` — `class <Kit>Provider(Provider)` (`scope = Scope.APP`);
     lifecycle — async-generator-`@provide` (yield ресурс; после yield — закрытие;
     fail-fast подключение до yield). Provider **не** provide'ит `Config` и не
     кладёт в контейнер пробы/джобы: их собирает App-Provider консьюмера в
     кортежи `healthkit.Probes` / `timerkit.Jobs` / `pgkit.MigrationSources`.
   - `tests/<kit>/test_*.py` — unit; I/O — через `testkit.start_*` с точными
     тегами образов (никогда `:latest`); тест Provider'а — сборка контейнера с
     минимальным набором провайдеров (аналог `fx.ValidateApp`); метрики/спаны —
     фикстуры `metric_reader`/`read_metric`/`span_exporter` из `tests/conftest.py`.
3. **Конфиг (если есть env-параметры).** Наследник `configkit.KitSettings` (ENV читается
   только по alias'ам, без `.env`) с `model_config = configkit.settings_config()` и
   `Field(validation_alias="ENV_NAME")` — это **public API** (имена полей и env-alias'ы).
   Кросс-полевые инварианты — `@model_validator(mode="after")`; `new_config()` =
   `configkit.build(Config, "<kit>", ConfigError)`: невалидный ENV валит старт, а
   не первый запрос. Zero-value конфиг либо безопасен и полезен, либо отвергается.
   Длительности — секунды (`float`), размеры — байты (`int`), секреты — `SecretStr`.
   Каждый alias описывается в README кита; endpoint-переменная
   (`*_URL`, `*_DSN`, `*_ENDPOINT`, `*_ADDR`, `*_TARGET`) — ещё и в
   `README.md § Per-concern endpoints` (`make envdoc-check`).
4. **Логирование и observability.** Логгер — `logkit.Logger` из контейнера.
   Метрики/спаны — через `pylibinfra._internal.otelkit` (`otelkit.meter("<kit>")`,
   `otelkit.tracer("<kit>")`, `otelkit.duration_histogram(...)`): инертны без
   провайдера у потребителя. Имена — `<kit_prefix>_<what>_<unit|total>`, label'ы —
   конечные множества. Инструменты создаются в конструкторе объекта кита, не на
   уровне модуля. Кит с инфраструктурной зависимостью (БД, брокер, кэш, объектное
   хранилище) отдаёт `probe(...) -> healthkit.Probe` (`healthkit.new`, при сетевом
   вызове — `healthkit.with_timeout`) для `/ready`.
5. **Ошибки и таймауты.** Каждый исходящий вызов — с таймаутом; всё, что может
   быть отменено, живёт в asyncio (CPU-тяжёлое — `asyncio.to_thread`).
6. **testkit-сабпакет (опц.).** Нужны helpers для тестов консьюмера — заведи
   `src/pylibinfra/testkit/<name>test/`, где `<name>` — имя кита **без** суффикса
   `kit` (`cachekit` → `testkit/cachetest/`). Внутри каталога кита test-хелперов
   не держим. Фейк держит контракт боевой реализации (валидирует вход так же).
7. **Документация.** Заведи `src/pylibinfra/<kit>/README.md` (назначение, импорт,
   быстрый старт, конфигурация, observability, API, тестирование, инварианты) —
   единый источник правды по киту; сниппет быстрого старта закрепи тестом
   `tests/<kit>/test_readme.py`. Для сложного кита разнеси детали по
   `src/pylibinfra/<kit>/docs/` (`architecture.md`, `configuration.md`,
   `observability.md`, `usage.md`, `testing.md`). Плюс строка в `README.md § Пакеты`
   (и в env-таблицу, если кит читает endpoint) и в карту пакетов `CLAUDE.md`.
   Без README kit «невидим».
8. **Gate.** `make check` зелёный перед claim «готово» (`make test-full`, если кит
   ходит в контейнер).

## Следующий шаг

После зелёного gate — релиз новой версии через `/release` (consumer'ы подтянут kit
по тегу `vX.Y.Z`; новый кит — добавление, значит minor). Новая сторонняя
зависимость в `pyproject.toml` — сперва согласуй с пользователем.
