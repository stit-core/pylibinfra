# tgbotkit

Bootstrap Telegram-бота на [aiogram](https://aiogram.dev) — то же, что `httpkit` для FastAPI:
`BotProvider` собирает `Bot`, `Dispatcher`, FSM-хранилище и `BotRunner`, вешает на `dp.update`
фиксированную цепочку (request-id, дедуп апдейтов, per-user троттлинг, recovery, RED-метрики и
спан) и проводит `aiogram_dialog`. Исходящие вызовы Bot API идут через session-middleware с
GCRA-лимитом, per-chat порядком и ретраем `429`.

Апдейты принимаются long-polling'ом или вебхуком поверх сервера `httpkit` — тот же процесс несёт
`/health` и `/ready`. Бот подключается данными: диалоги, команды и подписчики приезжают
кортежами `RouterBundle`/`CommandBundle`/`NotifierBundle`, тексты — через i18n. Подсистемы —
i18n (fluent), notifications (JetStream → Telegram), payments (Stars и фиат), webapp (`initData`).
Транспортные зависимости едут в extra `[tgbotkit]`.

## Импорт

```python
from pylibinfra import tgbotkit
```

Версия и установка — [корневой README](../../../README.md).

## Быстрый старт

Консьюмер кладёт в контейнер `Config`, `logkit.Logger`, `clockkit.Clock`, `redis.Redis`
(из `cachekit`) и `httpkit.Server`, собранный как `HttpProvider(autostart=False)` — его
lifecycle ведёт runner. Пробы кита складывает в `healthkit.Probes` тоже консьюмер:

```python
import redis.asyncio as redis
from aiogram import Bot
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import cachekit, healthkit, httpkit, logkit, tgbotkit
from pylibinfra.clockkit import ClockProvider

from mybot.dialogs import main_dialog


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def bot_config(self) -> tgbotkit.Config:
        return tgbotkit.new_config()  # BOT_* из ENV, невалидный — ConfigError

    @provide
    def http_config(self) -> httpkit.Config:
        return httpkit.new_config()

    @provide
    def cache_config(self) -> cachekit.Config:
        return cachekit.new_config()

    @provide
    def log_config(self) -> logkit.Config:
        return logkit.new_config()

    @provide
    def routers(self) -> tgbotkit.RouterBundle:
        return (main_dialog,)

    @provide
    def probes(self, bot: Bot, client: redis.Redis) -> healthkit.Probes:
        return (tgbotkit.probe(bot), tgbotkit.redis_probe(client))


container = make_async_container(
    tgbotkit.BotProvider(),
    httpkit.HttpProvider(autostart=False),  # порт занимает runner
    cachekit.CachekitProvider(),
    logkit.LogkitProvider(),
    ClockProvider(),
    AppProvider(),
)

runner = await container.get(tgbotkit.BotRunner)
await runner.run()  # держит процесс до SIGTERM и закрывает контейнер на выходе
```

Минимальный ENV: `BOT_TOKEN` (плюс `BOT_MODE=webhook` требует `BOT_WEBHOOK_BASE_URL` и
`BOT_WEBHOOK_SECRET`).

Без DI (свой lifecycle):

```python
runner = tgbotkit.build_runner(
    cfg=tgbotkit.new_config(),
    bot=bot,
    dp=dispatcher,
    container=container,
    client=redis_client,
    server=httpkit.Server(httpkit.new_config(), log, autostart=False),
    log=log,  # без него сбои шагов остановки уходят в никуда
)
await runner.run()
```

## Документация

- [docs/architecture.md](docs/architecture.md) — устройство: цепочка апдейта, runner'ы, точки
  расширения, подсистемы, ключевые решения (WHY).
- [docs/configuration.md](docs/configuration.md) — справочник `Config`/`I18nConfig`: все `BOT_*`,
  дефолты и правила валидации.
- [docs/observability.md](docs/observability.md) — метрики, labels, спан, ctx-поля логов, пробы.
- [docs/usage.md](docs/usage.md) — рецепты: диалоги и команды, i18n, уведомления из JetStream,
  платежи, WebApp, свой `ErrorUX`, вебхук рядом со своим API.
- [docs/testing.md](docs/testing.md) — `testkit/bottest`: flow-тесты диалогов, `MemoryRedis`,
  интеграционный lane.

## Инварианты

- **Runner ведёт HTTP-сервер.** `httpkit.HttpProvider` регистрируется с `autostart=False`;
  с автостартом `run()` падает `RunnerError`, чтобы два владельца не сервили один порт.
- **`BOT_TOKEN` обязателен**, формат проверяется на старте: нулевой конфиг бота не бывает
  безопасным. В webhook-режиме обязательны `https`-база и секрет — гейт вебхука fail-closed.
- **Telegram доставляет апдейты at-least-once.** Дедуп по `update_id` живёт в общем Redis, поэтому
  корректен при нескольких воркерах; `BOT_DEDUP_ENABLED=false` снимает защиту целиком.
- **Redis обязателен.** На нём FSM-состояние, дедуп, лимиты и event-isolation диалогов; при его
  недоступности кит fail-open по апдейтам (лучше дубль, чем немой бот), но `/ready` красный.
- **Polling не масштабируется** — один инстанс на токен; вебхук масштабируется, гонку за диалог
  снимает `RedisEventIsolation`.
- **Кит не знает о продукте.** Тексты, состояния, команды и маппинг «пользователь → чат» живут в
  сервисе; кит даёт только каркас и точки расширения.
