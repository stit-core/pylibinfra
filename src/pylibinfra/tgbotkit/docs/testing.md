# Тестирование бота

Сабпакет `pylibinfra.testkit.bottest` даёт unit-lane без Telegram и без Docker; интеграционный
lane поднимает Redis и NATS через `pylibinfra.testkit`.

## Flow-тест диалога

`BotClient` шлёт апдейты в диспетчер, `MockMessageManager` перехватывает отправку, `bind_dialogs`
повторяет проводку рантайма (`setup_dialogs` последним) с in-memory event-isolation:

```python
from pylibinfra.testkit.bottest import (
    BotClient,
    JsonMemoryStorage,
    MockMessageManager,
    bind_dialogs,
)


async def test_start_shows_menu() -> None:
    dp = Dispatcher(storage=JsonMemoryStorage())
    dp.include_router(main_dialog)
    dp.message.register(start, CommandStart())
    messages = MockMessageManager()
    bind_dialogs(dp, message_manager=messages)

    await BotClient(dp).send("/start")

    messages.assert_one_message()
    assert messages.last_message().text == "Меню"
```

`JsonMemoryStorage` гоняет данные диалога через JSON на каждой записи — `datetime`, `Decimal` или
ORM-объект в `dialog_data` падают `TypeError` в тесте, а не на первой записи в боевой Redis.

## MemoryRedis

Дубль `redis.Redis` в объёме, который использует кит: `SET` (`NX`/`EX`/`PX`), `GET`, `DELETE`,
`INCR`, `EXPIRE`, `TTL`, `PING`, `lock` и все четыре Lua-скрипта кита (`INCR_WINDOW_LUA`,
`GCRA_LUA`, `CAS_COMMIT_LUA`, `CAS_DELETE_LUA`). Годится для `DedupMiddleware`,
`ThrottleMiddleware`, `OutgoingThrottleMiddleware`, гвардов платежей, `build_storage` и
`build_isolation`:

```python
from pylibinfra.testkit.bottest import MemoryRedis

client = MemoryRedis()
mw = DedupMiddleware(client, 600, new_instruments())
```

Чужой Lua-скрипт `register_script` отвергает — фейк не должен молча расходиться с боевым Redis.
`MemoryRedis(fail=True)` роняет любую команду `RedisError` и проверяет fail-open-пути дедупа,
троттлинга и лимита исходящих.

TTL моделируются по `clockkit.Clock`: `MemoryRedis(clock=Tickable(START))` даёт истечение
маркеров дедупа, окон троттлинга и резервов платежей без реального сна. Дубли скриптов
повторяют боевые эффекты, включая приведение ответа Lua к целому и `INCR`, не продлевающий
окно. Живому Redis всё равно остаётся интеграционный lane: одни и те же ключи из двух
воркеров и то, что кит действительно кладёт в Redis ожидаемый `TTL`.

## Метрики и спаны

Общие фикстуры `tests/conftest.py`: `metric_reader`, `read_metric(name, attrs)`, `span_exporter`.
Провайдер ставится **до** создания объектов кита — инструменты создаются в конструкторе:

```python
async def test_duplicate_is_counted(read_metric) -> None:
    mw = DedupMiddleware(MemoryRedis(), 600, new_instruments())
    ...
    assert read_metric("tg_updates_deduped_total", {"type": "message"}) == 1
```

## Время

Ожидания rate-limit и `retry_after` идут через `clockkit.Clock`, поэтому тест двигает время сам:

```python
mw = OutgoingThrottleMiddleware(MemoryRedis(), cfg, log, clock=Tickable(START))
task = asyncio.create_task(mw(send, bot, method))
await clock.wait_sleepers(1)
clock.advance(1.0)
```

`validate_init_data` тоже принимает `clock=`, поэтому проверка свежести `initData` не зависит от
реальных часов.

## Runner целиком

Runner тестируется как есть: `httpkit.Server(Config(addr=":0"), autostart=False)` занимает
свободный порт, `runner.run()` живёт в задаче, `runner.stop()` её завершает. В webhook-режиме
POST на путь вебхука проверяет и гейт секрета, и путь `feed_update`:

```python
task = asyncio.create_task(runner.run())
async with httpx.AsyncClient(base_url=f"http://{server.addr()}") as client:
    assert (await client.post("/tg/hook", json=update)).status_code == 403
    assert (
        await client.post("/tg/hook", json=update, headers={SECRET_HEADER: SECRET})
    ).status_code == 200
runner.stop()
await task
```

В polling-режиме подменяются `dp.start_polling` и `dp.stop_polling` — Telegram в тесте не нужен.

## Интеграционный lane

Помечается `pytest.mark.integration`; контейнер — один на модуль:

```python
@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def redis_server() -> AsyncIterator[RedisFixture]:
    async with start_redis() as fixture:
        yield fixture
```

Через живой Redis проверяется то, чего фейк не покажет: общий дедуп двух воркеров, реальные TTL
маркеров и окон, TTL состояния диалога. Через `start_nats(jetstream=True)` — доставка события из
JetStream в `NotificationRouter` и то, что дренированная подписка больше не получает сообщений.
