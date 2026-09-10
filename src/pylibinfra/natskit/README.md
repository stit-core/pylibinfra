# natskit

Pub/sub поверх [NATS](https://github.com/nats-io/nats.py): интерфейс `Bus`
(Core NATS — fire-and-forget fan-out и load-balanced queue-группы) и `StreamBus`
(JetStream — персист, дедуп по `Nats-Msg-Id`, durable-консьюмеры с
poison-обработкой DLQ/`MaxDeliver`/NAK-backoff, deadline и `in_progress` для
медленных обработчиков). Типизированные хелперы `publish_json`/`subscribe_json`
(и stream-варианты) убирают ручной `json.dumps`/`json.loads`.

Из коробки: handle подписки (`Subscription` — `unsubscribe()`/`drain()`),
graceful `close()` с ожиданием обработчиков (`NATS_DRAIN_TIMEOUT`), deadline
обработчика (`NATS_HANDLER_TIMEOUT`), recovery упавшего обработчика,
backpressure семафором (`NATS_MAX_IN_FLIGHT`), логирование событий соединения
(slow consumer, disconnect/reconnect, исчерпание reconnect'ов) с редактированием
userinfo в URL, W3C trace-propagation через заголовки сообщения (consumer-спан —
child producer-спана) и метрики `nats_*`. Аутентификация — креды в `NATS_URL`,
`NATS_CREDS_FILE`, `NATS_NKEY_SEED_FILE`, TLS-корень `NATS_TLS_CA_FILE`.

Кит даёт сервису транспорт и надёжность доставки; доменная логика
(идемпотентность по бизнес-ключу, схемы сообщений, роутинг) — на стороне
потребителя.

## Импорт

```python
from pylibinfra import natskit
```

Версионирование и установка — в [корневом README](../../../README.md)
(§ Версионирование, § Стабильность API).

## Быстрый старт

Идиоматичный путь — `natskit.NatsProvider()`: dishka поднимает инстанс шины
(`Client`) и provide'ит `Conn`, `Bus` и `StreamBus`; `Config` регистрирует
приложение (asymmetry note). Закрытие контейнера делает graceful `close()`.

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import logkit, natskit


class AppConfigs(Provider):
    scope = Scope.APP

    @provide
    def log(self) -> logkit.Config:
        return logkit.new_config()

    @provide
    def nats(self) -> natskit.Config:
        return natskit.new_config()  # NATS_*


async def run() -> None:
    container = make_async_container(
        logkit.LogkitProvider(),
        natskit.NatsProvider(),  # Client + Conn + Bus + StreamBus
        AppConfigs(),
    )
    bus = await container.get(natskit.Bus)

    async def on_order(msg: dict[str, int]) -> None:
        del msg  # бизнес-обработка

    # Subscription позволяет остановить подписку раньше close(); хранить не обязательно.
    await natskit.subscribe_json(bus, "orders.created", on_order)
    await natskit.publish_json(bus, "orders.created", {"id": 42})

    await container.close()  # drain подписок → ожидание обработчиков → close
```

Минимальный ENV: `NATS_URL`. Конфиг валидируется при чтении (`new_config` →
`natskit.ConfigError`): пустой URL, отрицательный таймаут или недоступный файл
кредов валят запуск, а не первую публикацию. Без DI инстанс собирается
напрямую: `client = await natskit.new_client("", cfg, log)`.

## Документация

- [docs/architecture.md](docs/architecture.md) — устройство `Bus`/`StreamBus`, consumer reliability (deadline + `in_progress`, DLQ), декларация стримов, `Subscription` и graceful shutdown, trace-propagation, проектные решения (WHY).
- [docs/configuration.md](docs/configuration.md) — полный справочник ENV + тюнинг backpressure / DLQ / `MaxDeliver` / NAK-backoff / `AckWait` vs `HandlerTimeout` / управляемые стримы.
- [docs/observability.md](docs/observability.md) — метрики, labels, спаны, логи, health-проба (единый источник правды).
- [docs/usage.md](docs/usage.md) — рецепты: dishka-проводка, две шины в одном приложении, `publish_json`/`subscribe_json`, JetStream, DLQ/NAK, остановка подписки, чтение subject/headers.
- [docs/testing.md](docs/testing.md) — testcontainers NATS с JetStream, фейк шины `testkit.natstest`, что покрыто тестами кита.
