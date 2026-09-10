# Тестирование natskit

Тесты кита — интеграционные (реальный NATS в testcontainers) плюс юнит-тесты
чистых функций, конфига и логов соединения. Без Docker интеграционные
пропускаются (`testkit.require_docker`), юнит-тесты идут всегда.

## NATS-фикстуры

`testkit.start_nats(jetstream=True)` — async context-manager: контейнер с
фиксированным тегом образа (`testkit.NATS_IMAGE`), URL и хелперы поверх кита.
Один контейнер на модуль (или на прогон), изоляцию дают уникальные имена стримов
и subject'ов.

```python
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def nats_server() -> AsyncIterator[NatsFixture]:
    async with start_nats(jetstream=True) as fixture:
        yield fixture


async def test_delivery(nats_server: NatsFixture) -> None:
    async with nats_server.bus() as bus:  # Core-шина на контейнере
        await bus.publish("orders.created", b"{}")

    async with nats_server.stream_bus(jetstream={"stream_names": "ORDERS"}) as stream:
        await stream.publish_stream("ORDERS.created", b"{}", msg_id="1")
```

- `NatsFixture.url` / `.container` — адрес и handle контейнера.
- `NatsFixture.config(**overrides)` — `natskit.Config` на этот URL поверх
  `natskit.config_without_env(...)`: ENV не читается вовсе, поэтому `NATS_*`
  окружения на тест не влияют. Вложенные секции задаются словарём:
  `config(jetstream={"enabled": True, "ack_wait": 1.0})`; поле секции, переданное
  верхним уровнем (`config(ack_wait=1.0)`), — `natskit.ConfigError`, а не молча
  проигнорированный override.
- `NatsFixture.conn()` / `.bus()` / `.stream_bus()` — context-manager'ы: на
  выходе делают graceful `close()`.

Без `jetstream=True` поднимается Core NATS: JetStream-вызовы на таком сервере
вернут ошибку.

## Тесты потребителя без Docker

Для тестов бизнес-логики есть фейк шины `testkit.natstest.Recorder` — реализует и
`natskit.Bus`, и `natskit.StreamBus`, доставляя сообщения подписчикам синхронно:

```python
recorder = natstest.new()
await natskit.subscribe_stream_json(recorder, "ORDERS", "orders.>", "worker", handle_order)

# «пришло сообщение из шины» — обработчик уже отработал, когда deliver вернулся
# (сам deliver публикацией не считается и в published() не попадает)
await recorder.deliver("orders.created", b'{"id":1}', headers={"Event-Type": "order.created"})

published = recorder.published_on("orders.confirmed")
assert published[0].msg_id == "order-1"
assert published[0].metric_subject == "orders.*"  # без него publish заводит серию на каждый id
```

`set_publish_error(err)` включает ветку «шина недоступна», `handler_errors()` —
ошибки, которые подняли обработчики, `reset()` очищает записи между шагами.
`Message` несёт `subject`, `payload`, `headers`, `msg_id`, `metric_subject` и
`jetstream` — то же, что уехало бы в реальную шину.

## Что покрыто тестами кита

| Тест | Проверяет |
|---|---|
| `test_config.py` | дефолты, чтение ENV, пустая переменная = незаданная, изоляция префиксов инстансов, табличная валидация (URL, таймауты, файлы кредов, JetStream-поля), редактирование userinfo, `subject_matches`. |
| `test_publish.py` | `wire_headers` (копия + `Nats-Msg-Id`), `StreamMsg.spec`, `is_terminal` по цепочке причин, терминальность ошибки декода. |
| `test_conn.py` | текст ошибки подключения без пароля, ограниченный старт при бесконечных reconnect'ах, состояния `healthy`, логи disconnect/reconnect/async error/slow consumer с редактированным URL. |
| `test_bus.py` | round-trip и subject/headers из контекста, queue-группа без дублей, ошибка обработчика не валит подписку, backpressure, deadline обработчика, `unsubscribe`/`drain`, `close` (ожидание обработчиков, `ClosedError`, отмена по бюджету), пять метрик и связка producer→consumer-спанов. |
| `test_bus_unit.py` | ветки отказов Core-пути на фейке клиента: `PublishError`/`SubscribeError`, паника и отмена обработчика, `close` в бюджете при неразошедшемся drain'е подписки, недодренившаяся подписка остаётся под `close`. |
| `test_jetstream_unit.py` | ветки отказов JetStream-пути на фейках: декларация стримов, исходы батча, `ack`/`nak`/`term`, keepalive, остановка pull-цикла на закрытом соединении. |
| `test_jetstream.py` | round-trip, дедуп батча и per-message ошибки, DLQ по `MaxDeliver` (все заголовки копии), `TerminalError` и ошибка декода с первой попытки, отказ DLQ (`dlq_error`, без `term`), NAK-backoff, медленный обработчик без ределивери, зависший обработчик по `HandlerTimeout`, декларация стримов (создание, долив subject, drift, unmanaged), выключенный JetStream, фильтр поверх DLQ, метрики по фильтру подписки, drain/unsubscribe stream-подписки. |
| `test_module.py` | dishka-граф: `Client`/`Conn`/`Bus`/`StreamBus`, закрытие контейнера, две шины в разных компонентах, отказ на невалидном имени инстанса. |
| `test_readme.py` | сниппет быстрого старта из README действительно работает. |

## Запуск

```bash
uv run pytest tests/natskit -m "not integration"   # быстрые тесты
uv run pytest tests/natskit                        # полный прогон (нужен Docker)
```
