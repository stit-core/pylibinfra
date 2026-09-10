# httpclient

Исходящий HTTP-клиент для сервисов stit поверх `httpx`: retry с экспоненциальным
backoff, circuit breaker на каждый upstream-хост, политика редиректов, SSE-стримы
и OTel-инструментация (спан на вызов + спаны попыток на собственном httpx-клиенте,
метрики, `X-Request-ID` из контекста). Один `Client` обслуживает любое число хостов.

Поверх клиента лежит `ApiClient` — JSON-слой: декод тела в wire-модель (`model=`),
таксономия typed-ошибок (`NotFoundError`/`ConflictError`/`AuthError`/… ) и
`Idempotency-Key`. Именованные бэкенды (`ApiClients`) собираются из деклараций
`BackendConfig` — по клиенту со своей политикой на каждый upstream.

Клиент строго async: весь I/O — `asyncio`, таймаут задан всегда, запрос без
таймаута собрать нельзя.

## Импорт

```python
from pylibinfra import httpclient
```

Версионирование и установка — в [корневом README](../../../README.md)
(§ Версионирование, § Стабильность API).

## Быстрый старт

`HttpClientProvider` требует в графе `httpclient.Config` — его регистрирует
консьюмер (asymmetry note). Собственный транспорт и hook перед попыткой —
опциональные зависимости: свой Provider регистрируется ПОСЛЕ и перекрывает
дефолт.

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import httpclient


class AppConfigs(Provider):
    scope = Scope.APP

    @provide
    def http_config(self) -> httpclient.Config:
        return httpclient.new_config()  # HTTPCLIENT_* из ENV


class RateLimited(Provider):
    scope = Scope.APP

    @provide
    def before_attempt(self) -> httpclient.OptionalBeforeAttempt:
        async def wait(attempt: int) -> None:
            await limiter.acquire()  # per-host rate-limiter приложения

        return wait


container = make_async_container(
    httpclient.HttpClientProvider(),
    AppConfigs(),
    RateLimited(),
)
client = await container.get(httpclient.Client)

response = await client.get("https://api.example.com/v1/items")
# 5xx после ретраев — это ответ, статус разбирает вызывающий
if response.status_code == 200:
    items = response.json()
```

Без DI:

```python
from pylibinfra import httpclient

async with httpclient.new(httpclient.new_config()) as client:
    response = await client.get("https://api.example.com/v1/items")
```

Несколько клиентов с разной политикой в одном процессе — `new_config_prefixed`:
`new_config_prefixed("AMOCRM")` читает `<PREFIX>_HTTPCLIENT_*` и падает обратно на
голые `HTTPCLIENT_*` для незаданных переменных (базовая политика сервиса плюс
точечные переопределения на upstream).

## Конфигурация

`httpclient.Config`, `httpclient.BreakerConfig`, `httpclient.BackendsConfig` и
`httpclient.BackendConfig` — public API (см. корневой `README.md`
§ Стабильность API). `new_config`/`new_config_prefixed`/`new_breaker_config`/
`new_backends_config` читают ENV и переводят ошибку валидации в
`httpclient.ConfigError`; прямой `Config(...)` даёт pydantic `ValidationError`
(тоже `ValueError`). Валидатор отвергает отрицательные значения и подставляет
дефолты вместо нулей — `Config()` даёт тот же клиент, что и пустое окружение.

| Env | Поле | Тип | Default | Правило валидации |
|---|---|---|---|---|
| `HTTPCLIENT_TIMEOUT` | `Config.timeout` | float, секунды | `10` | `< 0` — ошибка; `0` → `10`. Таймаут одной попытки, не всего цикла retry; для `stream` — только фаза соединения и заголовков |
| `HTTPCLIENT_MAX_RETRIES` | `Config.max_retries` | int | `3` | `< 0` — ошибка; `0` — без retry (осознанный выбор, дефолт не подставляется) |
| `HTTPCLIENT_RETRY_WAIT` | `Config.retry_wait` | float, секунды | `0.2` | `< 0` — ошибка; `0` → `0.2`. База backoff: `retry_wait·2^(attempt-1)` + jitter `[0, retry_wait)` |
| `HTTPCLIENT_RETRY_MAX_WAIT` | `Config.retry_max_wait` | float, секунды | `5` | `< 0` или `< retry_wait` — ошибка; `0` → `5`. Потолок паузы, в том числе для `Retry-After` |
| `HTTPCLIENT_RETRY_NON_IDEMPOTENT` | `Config.retry_non_idempotent` | bool | `false` | Ретраить POST/PATCH без явного разрешения (см. § Retry) |
| `HTTPCLIENT_MAX_REDIRECTS` | `Config.max_redirects` | int | `10` | `< 0` — ошибка; `0` → `10`. Длиннее цепочка — `TooManyRedirectsError` |
| `HTTPCLIENT_ALLOW_CROSS_HOST_REDIRECTS` | `Config.allow_cross_host_redirects` | bool | `false` | `false` — редирект на другой хост это `CrossHostRedirectError` (см. § Редиректы) |
| `HTTPCLIENT_SSE_MAX_EVENT_BYTES` | `Config.sse_max_event_bytes` | int, байты | `1048576` | `< 0` — ошибка; `0` → 1 MiB. Потолок строки и события SSE (`EventTooLargeError`) |
| `HTTPCLIENT_BASE_URL` | `Config.base_url` | str | `""` | Базовый URL относительных путей (`client.get("/v1/items")`) |
| `HTTPCLIENT_BREAKER_FAILURE_THRESHOLD` | `BreakerConfig.failure_threshold` | int | `5` | `< 0` — ошибка; `0` выключает breaker (осознанный выбор) |
| `HTTPCLIENT_BREAKER_RECOVERY_TIMEOUT` | `BreakerConfig.recovery_timeout` | float, секунды | `30` | `< 0` — ошибка; `0` → `30`. Сколько цепь держится в `open` до пробного запроса |
| `HTTPCLIENT_BACKENDS` | `BackendsConfig.backends` | JSON `{"<имя>": BackendConfig}` | `{}` | Обязателен `base_url`; неизвестные поля отвергаются |

`BackendConfig` повторяет поля `Config` (кроме `base_url` — он обязателен, и
порогов цепи — они в `BackendBreakerConfig`) и добавляет `headers` — статические
заголовки бэкенда (`Authorization: Bearer …`). Это plain-модель, а не
`BaseSettings`: глобальные `HTTPCLIENT_*` в декларацию именованного бэкенда не
протекают. `BackendConfig.client_config()` собирает из декларации runtime-`Config`.

## Retry

Повтор делается, только когда он безопасен:

- метод идемпотентен (`GET`, `HEAD`, `OPTIONS`, `PUT`, `DELETE`, `TRACE`), либо
  запрос явно разрешил повтор: блок `with httpclient.allow_retry():`, заголовок
  `Idempotency-Key`, либо `HTTPCLIENT_RETRY_NON_IDEMPOTENT=true`;
- тело воспроизводимо: `bytes`/`str`/`json=`/`data=` — да; multipart (`files=`),
  асинхронный генератор или поток (`content=<async iterator>`) — нет.

Ретраятся статусы `408`, `429`, `502`, `503`, `504` и транспортные ошибки
(`httpx.TransportError`), включая таймаут попытки — он ограничен
`asyncio.timeout` и после исчерпания ретраев поднимается как `TimeoutError`.
`500` не ретраится: у большинства upstream'ов он детерминирован, повтор только
жжёт бюджет.

Не ретраятся: отмена вызывающего (`asyncio.CancelledError`), ошибка hook'а
`before_attempt` и перманентные ошибки транспорта — проверка сертификата
(`ssl.SSLError` в цепочке причин), неподдерживаемая схема URL
(`httpx.UnsupportedProtocol`), превышенный лимит редиректов.

Пауза — `Retry-After` сервера (секунды или HTTP-дата, максимум 5 минут), иначе
экспоненциальный backoff с jitter; и то и другое под `retry_max_wait`. Разбор
заголовка экспортирован — `httpclient.parse_retry_after(value, now)`: одно
правило RFC 9110 на всех потребителей заголовка. После исчерпания ретраев
возвращается **последний ответ** с его статусом и телом — статус разбирает
вызывающий.

`before_attempt` вызывается перед каждой попыткой, включая первую, с номером
попытки от нуля: место для per-host rate-limiter'а. Ошибка hook'а прерывает
запрос, поднимается вызывающему как есть и провалом цепи не считается.

## Circuit breaker

Цепь заводится на каждый `host[:port]` запроса: отказ одного upstream не
размыкает цепь остальным. Машина состояний:

| Состояние | Что делает вызов | Переход |
|---|---|---|
| `closed` | идёт в upstream | `failure_threshold` подряд идущих провалов → `open` |
| `open` | `BreakerOpenError` без запроса | через `recovery_timeout` первый же вызов → `half-open` |
| `half-open` | в upstream уходит один пробный запрос, параллельные получают `BreakerOpenError` | успех пробы → `closed`, провал → `open` |

Успех обнуляет счётчик подряд идущих провалов. Провалом считается транспортная
ошибка (включая таймаут попытки, отказ TLS и нарушение redirect-политики) или
ответ `502`/`503`/`504` после ретраев — сигналы «хоста нет». `500`/`501` от
живого upstream'а провалом не считаются, как и отмена вызывающего и ошибка
`before_attempt`: всплеск barge-in или ожидания лимитера не должен отключать
здоровый хост — такой исход не двигает счётчик ни в одну сторону.

Время цепь берёт из `clockkit.Clock` (`Client(cfg, clock=…)`, дефолт `Real`):
тест двигает `Tickable` и проверяет восстановление без ожидания реального
времени. `HTTPCLIENT_BREAKER_FAILURE_THRESHOLD=0` выключает цепь целиком.

## Редиректы

Редиректы следуются в пределах `max_redirects` и **только в пределах исходного
хоста**: кросс-хостовый редирект — `CrossHostRedirectError`. Так API-клиент не
уносит секреты на чужой хост при компрометации DNS или upstream'а. Возврат на
исходный хост в середине цепочки кросс-хостовым не считается.

`HTTPCLIENT_ALLOW_CROSS_HOST_REDIRECTS=true` разрешает переход, но на чужой хост
уезжают только `Accept`, `Accept-Encoding`, `Accept-Language`, `Content-Type`,
`Content-Length`, `Referer`, `User-Agent`, `X-Request-ID` и транспортные
`Host`/`Connection`/`Transfer-Encoding` (их httpx считает на каждый хоп сам) —
все остальные заголовки, в том числе кастомные ключи и подписи, снимаются.

## SSE

`stream(method, url, ...)` открывает `text/event-stream` и отдаёт `SSEStream` —
async-итератор `Event(id, event, data)`. У стрима нет retry и цепи: частично
прочитанный поток неретраябелен, а `open`-состояние цепи рассчитано на короткие
запросы.

- `timeout` ограничивает **только соединение и ожидание заголовков**
  (`HeaderTimeoutError`); тело живёт, пока его читают, и закрывается `aclose()`
  или выходом из `async with`. Фоновых задач у потока нет — читает тот, кто
  итерируется.
- Ответ не с `Content-Type: text/event-stream` отвергается
  (`NotEventStreamError`), неуспешный статус — `StatusError` с усечённым до
  2 KiB телом.
- Строка и событие ограничены `sse_max_event_bytes`; превышение поднимает
  `EventTooLargeError` из итератора.
- Парсинг по спеке WHATWG: терминатор строки — CR, LF или CRLF; `data` склеены
  через `\n`; блок без `data` не диспатчится и сбрасывает тип события; `id`
  живёт между событиями (`Event.id`, `SSEStream.last_event_id`); `retry` доступен
  как `SSEStream.retry` (секунды) — реконнект httpclient не делает сам.
- Терминатор потока вроде `[DONE]` приходит обычным событием: полезную нагрузку
  интерпретирует вызывающий.

```python
async with await client.stream("POST", "/v1/chat", json=payload) as stream:
    async for event in stream:
        if event.data == "[DONE]":
            break
        handle(event.data)
```

## Observability

Instrumentation-scope `pylibinfra.httpclient`, глобальные OTel-провайдеры (no-op
без SDK консьюмера).

| Сигнал | Тип | Labels | Назначение |
|---|---|---|---|
| `http_client_request_duration_seconds` | Histogram (`s`) | `host`, `method`, `status`, `mode` | Длительность вызова целиком, включая ретраи и backoff; `mode="sse"` только у `stream` (у `request` label отсутствует) |
| `http_client_retry_attempts_total` | Counter | `host` | Инкремент на каждый состоявшийся повтор (после ожидания backoff) |
| `http_client_breaker_transitions_total` | Counter | `host`, `from`, `to` | Переходы состояния цепи хоста (`closed`/`open`/`half-open`) |
| `http_client_breaker_open` | UpDownCounter | `host` | `1`, пока цепь хоста открыта |
| `httpclient.Do <METHOD>` | Span (client) | `http.request.method`, `server.address`, `url.full`, `http.response.status_code` | Спан на вызов: покрывает все попытки и паузы между ними |
| `httpclient.Stream <METHOD>` | Span (client) | те же | Спан на весь стрим: закрывается вместе с потоком |
| HTTP-спан попытки | Span (`HTTPXClientInstrumentor`) | semconv httpx | Дочерний спан на каждый запрос транспорта, W3C trace-context в upstream |

Спаны попыток даёт `HTTPXClientInstrumentor.instrument_client`, применённый к
собственному `httpx.AsyncClient` кита в конструкторе и снятый в `aclose()`:
httpx процесса кит не патчит, поэтому клиенты консьюмера, созданные напрямую,
спанов попыток не получают.

Значения `status`: код ответа (`200`, `503`), `breaker_open` при отказе открытой
цепи, `error` для транспортных ошибок и отмены вызывающего. Для `stream`
`status` — код ответа при чистом завершении потока и `error`, если поток
оборвался. `host` ограничен числом upstream'ов сервиса; `method`, `status`,
`mode` — конечные множества. Гистограмма использует общие бакеты длительностей
pylibinfra.

`url.full` уходит в спан без query и userinfo. Заголовок `X-Request-ID`
проставляется из `_internal.requestid.current()`, если вызывающий не задал свой.

## API

```python
class Client:
    def __init__(cfg: Config, *, transport=None, before_attempt=None, headers=None, clock=None)
    config: Config
    async def request(method: str, url: str, **kwargs) -> httpx.Response
    async def get/post/put/patch/delete(url: str, **kwargs) -> httpx.Response
    async def stream(method: str, url: str, **kwargs) -> SSEStream
    def breaker_state(host: str) -> State            # состояние цепи хоста
    async def aclose() -> None                       # + async with

def new(cfg: Config | None = None, *, transport=None, before_attempt=None,
        headers=None, clock=None) -> Client

BeforeAttempt = Callable[[int], Awaitable[None]]
def allow_retry() -> ContextManager[None]
def parse_retry_after(value: str, now: datetime) -> float | None

class SSEStream:                                     # async-итератор Event
    last_event_id: str
    retry: float
    async def aclose() -> None                       # + async with

@dataclass(frozen=True)
class Event:
    id: str
    event: str
    data: str

class State(StrEnum): CLOSED | OPEN | HALF_OPEN      # состояние цепи хоста

HEADER_IDEMPOTENCY_KEY = "Idempotency-Key"           # заголовок, разрешающий повтор мутации
MAX_RETRY_AFTER = 300.0                              # потолок паузы из Retry-After, секунды

def new_config() -> Config
def new_config_prefixed(prefix: str) -> Config
def new_breaker_config() -> BreakerConfig
def new_backends_config() -> BackendsConfig

class HttpClientProvider(Provider)                   # Client + ApiClient, aclose на стопе
class ApiClientsProvider(Provider)                   # ApiClients из BackendsConfig
OptionalTransport = httpx.AsyncBaseTransport | None  # override-дефолт
OptionalBeforeAttempt = BeforeAttempt | None         # override-дефолт

# Исключения: HttpClientError (база), ConfigError, BreakerOpenError,
# TooManyRedirectsError, CrossHostRedirectError, NotEventStreamError,
# EventTooLargeError, HeaderTimeoutError, StatusError(status, body)
```

## ApiClient и ApiClients

`ApiClient` — Python-специфичный JSON-слой поверх `Client`: тело 2xx декодится в
`dict`/`list` либо в pydantic-модель через `model=`, статус превращается в
typed-ошибку, `idempotency_key=` кладётся в заголовок `Idempotency-Key` (и тем
самым разрешает повтор мутации).

| Исход | Исключение |
|---|---|
| `401` | `AuthError` |
| `403` | `ForbiddenError` |
| `404` | `NotFoundError` |
| `409` | `ConflictError` |
| `429` | `RateLimitedError` (подкласс `UnavailableError`, `retry_after` из заголовка) |
| прочие `4xx` | `InvalidRequestError` |
| `5xx` после ретраев | `UnavailableError` |
| транспорт, таймаут попытки (`TimeoutError`), `BreakerOpenError`, нарушение redirect-политики | `UnavailableError` |
| 2xx с не-JSON телом или несоответствие `model=` | `ContractError` (подкласс `UnavailableError`) |

База таксономии — `ApiError`; у каждой ошибки есть контекст вызова: `method`,
`path`, `status_code`, `retry_after`, `body_snippet` (256 байт).

`ApiClients` — именованные бэкенды: `ApiClientsProvider` собирает по клиенту на
каждую запись `BackendsConfig.backends`, `clients.get("crm")` отдаёт
`ApiClient`, необъявленное имя — `UnknownBackendError` со списком известных.

## Тестирование

Тесты кита идут без сети: `httpx.MockTransport` для политики попыток, цепи и
редиректов и локальный `asyncio.start_server` там, где нужен настоящий поток
(SSE, таймауты соединения, chunked-тело). Для тестов консьюмера удобны
`Client(cfg, transport=httpx.MockTransport(handler))` и
`Client(cfg, clock=clockkit.Tickable(...))` — восстановление цепи проверяется без
ожидания реального времени.

## Инварианты и подводные камни

- **5xx — не ошибка `request`.** Исключение только у транспорта, отмены,
  нарушения redirect-политики и открытой цепи; статус разбирает вызывающий. Тело
  последнего ответа читаемо.
- **POST не ретраится по умолчанию.** Дубли на стороне сервера дороже повтора;
  разрешайте явно через `allow_retry()`/`Idempotency-Key`, когда сервер
  дедуплицирует.
- **Воспроизводимость тела вместо `GetBody`.** Go ретраит запрос, у которого есть
  `GetBody`; httpx-эквивалент — прочитанное тело (`request.content` доступен).
  Тело-генератор (`content=<async iterator>`) и multipart (`files=`) остаются
  потоком, поэтому такой запрос не ретраится ни при каком флаге: буферизовать
  загрузку файла в память ради повтора дороже, чем повторить её вызывающему.
- **`timeout` — на попытку.** Общее время `request` может достигать
  `(max_retries+1)·timeout + Σ backoff`; ограничивайте вызов своим
  `asyncio.timeout`.
- **Цепь считает 502/503/504 провалом.** Upstream, стабильно отвечающий `503`,
  разомкнёт цепь через `failure_threshold` вызовов; полностью выключить —
  `HTTPCLIENT_BREAKER_FAILURE_THRESHOLD=0`.
- **Не для user-supplied URL.** Цепь и label `host` заводятся на каждый хост
  запроса: на webhook'ах по чужим адресам это рост словаря цепей и
  кардинальности метрик. Нужен исходящий вызов по адресу пользователя — держите
  для него отдельный `Client` с `HTTPCLIENT_BREAKER_FAILURE_THRESHOLD=0`,
  валидируйте хост до вызова и не переиспользуйте клиент основных upstream'ов.
- **Секреты не в query.** `url.full` уходит в спан вызова и в текст ошибки
  `ApiClient` без query и userinfo, но спан попытки от `HTTPXClientInstrumentor`
  пишет URL с query (userinfo он вычищает сам) — передавайте токены заголовками.
- **`X-Request-ID`** ставится из contextvar, только если вызывающий не задал свой
  заголовок.
- **`aclose()` обязателен при ручной сборке.** `HttpClientProvider` закрывает
  клиент на стопе; собранный руками закрывайте сами (`async with` или
  `await client.aclose()`) — он держит собственный пул соединений. Для `SSEStream`
  то же: незакрытый поток держит соединение до сборки мусора.
- **Инструментация — на свой клиент, не на процесс.** Спаны попыток вешаются на
  транспорт того `httpx.AsyncClient`, который создал `Client`, и снимаются в
  `aclose()`. Один и тот же объект транспорта в двух `Client` инструментируется
  дважды — передавайте каждому свой (`transport=None` создаёт его сам).
