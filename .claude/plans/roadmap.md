# Roadmap

Единственный артефакт планирования — этот файл. Подпапок, `<slug>.md`, архивов и
changelog'ов нет.

**Planning-first.** Изменение идёт через план — `### <slug>`-секцию под `## Active`.
Прямая правка без плана допустима только для truly-atomic (опечатка, один очевидный файл)
и багфиксов (RED-first). Отклонение от плана — осознанное, проговаривается с юзером.

**Жизненный цикл.** `planning → in-progress → blocked? → done`. По `done` секция удаляется
целиком — история остаётся в `git log`. Незакрытый follow-up переезжает в бэклог.

**Где это запускается.** Репо — библиотека, живого стека нет: гейт локальный —
`make check` (fmt, lint, mypy strict, unit-тесты, envdoc-check), перед тегом —
`make gate-clean` (то же в чистом worktree) и `make test-full` (testcontainers).
Выпуск версии — тег `vX.Y.Z` (skill `/release`), консьюмеры подтягивают `uv add … @vX.Y.Z` сами.

## Active

Пусто — активных планов нет.

## Backlog

- **Миграция потребителей на версию с паритетом golibinfra v1.1.0.** Источник правды по
  шагам — аннотация тега (`git tag -l --format='%(contents)' vX.Y.Z`): breaking затрагивает
  каждый кит (KitSettings/ConfigError, healthkit.Probes, timerkit.Jobs, Logger/Clock из
  владельцев, sentinel'ы `*Error`, метрики `pg_pool_*`/`authn_*`, testkit `pgtest`/`outboxtest`).
  Консьюмер обязан регистрировать `LogkitProvider()`, `ClockProvider()`, а бот — ещё
  `HttpProvider(autostart=False)`.
- **Уязвимость aiohttp 3.13.5 (14 advisories PYSEC-2026-*).** Фикс — aiohttp ≥ 3.14, но
  aioboto3 15.5.0 (последний) пинит `aiobotocore==2.25.1` с `aiohttp<3.14`. Триггер —
  релиз aioboto3 на aiobotocore ≥ 3.x; тогда `uv lock --upgrade-package aioboto3` и `make vuln`.
- **NATS creds/nkey-аутентификация** (`NATS_CREDS_FILE`, `NATS_NKEY_SEED_FILE`) требует
  пакет `nkeys` (`nats-py[nkeys]`), которого нет в зависимостях; ENV поддержаны и описаны
  в `natskit/docs/configuration.md`. Добавление extra — решение оператора.
- **Киты golibinfra без Python-аналога:** `tzkit`, `oidckit`, `hubkit`, `anthropickit`,
  `amocrmkit`, `grpcclient` — заводить по потребности консьюмеров (`/add-kit`).
- **CI на Gitea Actions**, когда появится раннер: `make check` + `make test-full`.
  До раннера гейт держит `.githooks/pre-push` (`make hooks`).
- **`tests/conftest.py::read_metric` собирает метрики на loop'е теста.** Observable-инструмент
  поверх async-источника обязан не блокировать вызывающий loop (как `outboxkit` backlog);
  при появлении второго такого инструмента — вынести сбор в `asyncio.to_thread`.
