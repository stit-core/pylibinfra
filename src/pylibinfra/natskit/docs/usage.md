# Использование natskit

Рецепты по `Bus` (Core NATS) и `StreamBus` (JetStream). Сигнатуры — точно как в
коде (`bus.py`, `jetstream.py`, `subscription.py`, `context.py`).

## Сигнатуры API

### Bus (Core NATS)

```python
Handler = Callable[[bytes], Awaitable[None]]

class Bus(Protocol):
    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        msg_id: str | None = None,
        headers: Mapping[str, str] | None = None,
        metric_subject: str | None = None,
    ) -> None: ...
    async def subscribe(self, subject: str, handler: Handler) -> Subscription: ...
    async def subscribe_queue(self, subject: str, queue: str, handler: Handler) -> Subscription: ...


def new_bus(conn: Conn, cfg: Config | None = None, log: Logger | None = None) -> Bus


# Дженерики: json.dumps/json.loads за вызывающего.
async def publish_json[T](bus: Bus, subject: str, msg: T, *, msg_id=None, headers=None, metric_subject=None) -> None
async def subscribe_json[T](bus: Bus, subject: str, handler: Callable[[T], Awaitable[None]]) -> Subscription
async def subscribe_queue_json[T](bus: Bus, subject: str, queue: str, handler) -> Subscription
```

`publish` принимает готовые байты — `Bus` и `StreamBus` симметричны, а JSON
добавляют дженерики.

### Опции публикации

```python
msg_id="order-42"          # Nats-Msg-Id: дедуп стримом в окне NATS_JS_DUPLICATES
headers={"Event-Type": …}  # прикладные заголовки; обработчик читает headers_from_context()
metric_subject="orders.*"  # значение label `subject` publish-метрик (шаблон, не конкретный id)

# Разобранные опции — для фейков шины вне кита (testkit.natstest).
class PublishSpec:
    msg_id: str
    headers: Mapping[str, str]
    metric_subject: str

    def wire_headers(self) -> dict[str, str]  # копия заголовков + Nats-Msg-Id


def new_publish_spec(*, msg_id=None, headers=None, metric_subject=None) -> PublishSpec
```

### StreamBus (JetStream)

```python
class StreamBus(Protocol):
    async def publish_stream(
        self, subject: str, payload: bytes, *, msg_id=None, headers=None, metric_subject=None
    ) -> None: ...
    async def publish_stream_batch(self, msgs: Sequence[StreamMsg]) -> list[Exception | None]: ...
    async def subscribe_stream(
        self, stream: str, subject: str, durable: str, handler: Handler
    ) -> Subscription: ...


async def new_stream_bus(conn: Conn, cfg: Config, log: Logger | None = None) -> StreamBus


@dataclass(frozen=True, slots=True)
class StreamMsg:
    subject: str
    payload: bytes
    msg_id: str = ""            # → Nats-Msg-Id
    headers: Mapping[str, str] = {}
    metric_subject: str = ""    # label `subject` publish-метрик


async def publish_stream_json[T](bus: StreamBus, subject: str, msg: T, *, msg_id=None, …) -> None
async def subscribe_stream_json[T](bus: StreamBus, stream: str, subject: str, durable: str, handler) -> Subscription
```

### Subscription

```python
class Subscription(Protocol):
    async def unsubscribe(self) -> None                          # снять интерес сразу, обработчики не ждутся
    async def drain(self, *, timeout: float | None = None) -> None  # снять интерес, дообработать буфер, дождаться обработчиков
```

### Клиент, соединение и ошибки

```python
async def new_client(name: str, cfg: Config, log: Logger | None = None) -> Client
client.bus, client.stream, client.conn, client.name
client.healthy() -> None
await client.close(timeout=None)

async def new_conn(cfg: Config, log=None, *, instance: str = DEFAULT_INSTANCE) -> Conn
def probe(conn: Conn) -> healthkit.Probe

class NatskitError(Exception)             # база ошибок кита
class ConfigError(ValueError)             # невалидный конфиг (new_config/new_config_prefixed/NatsProvider)
class ClosedError(NatskitError)           # подписка после начала close()
class ConnectionClosedError(NatskitError) # соединение закрыто окончательно (healthy/probe)
class PublishError(NatskitError)          # публикация не ушла
class SubscribeError(NatskitError)        # подписку не удалось зарегистрировать
class StreamError(NatskitError)           # стрим/консьюмер недоступен
class JetStreamDisabledError(NatskitError)
class TerminalError(NatskitError)         # маркер «не ретраить» для обработчика
def is_terminal(exc: BaseException | None) -> bool
```

### Контекст обработчика

```python
def subject_from_context() -> str          # реальный subject входящего; "" вне обработчика
def headers_from_context() -> dict[str, str]  # заголовки входящего; {} вне обработчика

# Для фейков шины: кладёт subject и заголовки так, как это делает кит.
with message_context(subject, headers): ...

# Накрывает ли фильтр (с * и >) конкретный subject — та же семантика, что у подписки.
def subject_matches(filter_: str, subject: str) -> bool
```

## Подключение через dishka

`NatsProvider()` поднимает инстанс шины и provide'ит `Client`, `Conn`, `Bus` и
`StreamBus`. Конфиг регистрирует приложение.

```python
container = make_async_container(
    logkit.LogkitProvider(),
    natskit.NatsProvider(),
    AppConfigs(),  # natskit.Config + logkit.Config
)
bus = await container.get(natskit.Bus)
stream = await container.get(natskit.StreamBus)
conn = await container.get(natskit.Conn)

probes = (healthkit.with_timeout(natskit.probe(conn), 2.0),)  # в кортеж healthkit.Probes
await container.close()  # graceful close инстанса
```

## Две шины в одном приложении

Своя шина и внешняя (например, hub соседней команды) живут в разных
dishka-компонентах: конфиг именованного инстанса читается из `HUB_NATS_*`, а
`Logger` берётся из дефолтного компонента.

```python
class HubConfig(Provider):
    scope = Scope.APP

    @provide
    def cfg(self) -> natskit.Config:
        return natskit.new_config_prefixed("hub")  # HUB_NATS_*


container = make_async_container(
    logkit.LogkitProvider(),
    AppConfigs(),  # natskit.Config (голые NATS_*)
    natskit.NatsProvider(),  # своя шина
    HubConfig(component="hub"),
    natskit.NatsProvider("hub", component="hub"),  # внешняя шина
)

own = await container.get(natskit.Bus)
hub = await container.get(natskit.Bus, component="hub")
```

Имя инстанса едет в label `instance` метрик и спанов, поэтому сигналы двух шин не
сливаются. Незаданная `HUB_NATS_*` берёт дефолт кита, а не голую `NATS_*`.

## Публикация и подписка

```python
async def on_order(msg: OrderCreated) -> None:
    subject = natskit.subject_from_context()  # orders.42.created для фильтра orders.*.created
    event_type = natskit.headers_from_context().get("Event-Type", "")
    ...


sub = await natskit.subscribe_json(bus, "orders.*.created", on_order)
await natskit.publish_json(
    bus,
    "orders.42.created",
    OrderCreated(id=42),
    headers={"Event-Type": "order.created"},
    metric_subject="orders.*.created",
)

await sub.drain(timeout=5)  # остановить подписку раньше close(), дождавшись обработчиков
```

Load-balanced потребление — `subscribe_queue_json(bus, subject, "workers", handler)`:
сообщение получит ровно один инстанс queue-группы.

## JetStream

```python
stream = await container.get(natskit.StreamBus)

await natskit.publish_stream_json(
    stream, "ORDERS.created", event, msg_id=f"order-{event.id}", metric_subject="orders"
)

results = await stream.publish_stream_batch(
    [natskit.StreamMsg(subject="ORDERS.created", payload=data, msg_id="order-1")]
)
if results[0] is not None:  # None — персист подтверждён PubAck'ом
    ...

await natskit.subscribe_stream_json(stream, "ORDERS", "ORDERS.created", "billing", handle)
```

`durable` (`"billing"`) — имя консьюмера: несколько инстансов сервиса с одним
durable делят нагрузку и продолжают с того же места после рестарта.

## Ретраить или нет

Ошибка обработчика по умолчанию означает «попробуй ещё»: JetStream делает `nak` с
задержкой `NATS_JS_NAK_BACKOFF`, пока не исчерпан `NATS_JS_MAX_DELIVER`. Если
ретрай бессмыслен (чужая схема, битый payload), обработчик поднимает
`natskit.TerminalError` — кит уводит сообщение в DLQ и делает `term` с первой
попытки:

```python
async def handle(msg: OrderCreated) -> None:
    try:
        await service.apply(msg)
    except UnknownSchema as exc:
        raise natskit.TerminalError("orders: unknown schema") from exc
```

Ошибка декода в `subscribe_*_json` уже терминальна. Маркер ищется по цепочке
`raise … from` (`natskit.is_terminal`).

## Остановка

```python
await sub.unsubscribe()  # снять интерес немедленно
await sub.drain(timeout=5)  # снять интерес и дождаться обработчиков
await client.close(timeout=5)  # весь инстанс: drain подписок → обработчики → flush → close
```

`NatsProvider` делает `close()` сам при закрытии контейнера; ручной вызов нужен
только в коде без DI.
