# httpkit — тестирование

## Хендлеры через testkit/handlertest

`testkit/handlertest` поднимает FastAPI с обработчиками ошибок кита и бьёт в него ASGI-транспортом,
без сети и сокетов:

```python
from pylibinfra.testkit import handlertest


def register(app: FastAPI) -> None:
    register_routes(app, svc)


async def test_get_user() -> None:
    app = handlertest.new_app(register)
    async with handlertest.new_client(app) as client:
        resp = await client.get("/users/42")
        handlertest.assert_status(resp, 200)

        resp = await client.get("/users/missing")
        handlertest.assert_api_error(resp, 404, "USER_NOT_FOUND")
```

Хелперы: `new_app`, `new_client`, `assert_status`, `assert_api_error`, `assert_json`,
`decode_api_error`. `handlertest.APIError` — структура для декода, не `httpkit.APIError`: она
покрывает оба тела сразу — плоский problem+json (`type`, `title`, `status`, `detail`, `instance`,
`code`) и ошибку валидации, где список проблем приходит в `errors: list[APIErrorLocation]`
(`message`, `location`, `value`).

## Middleware напрямую

Каждый middleware — обычный ASGI-класс, тестируется на голом приложении:

```python
app = FastAPI()
app.add_middleware(httpkit.CORSMiddleware, cfg=httpkit.CORSConfig(origins=["https://a.example"]))

async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
    resp = await c.options("/", headers={"Origin": "https://a.example"})
```

Для `RateLimit` задайте детерминированные часы (`clockkit.Tickable`) или `rps=1`, чтобы вторая
попытка гарантированно упёрлась в bucket; для `client_ip` с `trust_proxy` ключом станет **правое**
значение `X-Forwarded-For`.

Строки `AccessLog` проверяются `testkit/logtest`: `Capture` реализует `logkit.Logger` и копит
записи в памяти — `entries()`, `find(msg, level=...)`, `has(msg)`, `Entry.value(key)`.

## Метрики и спаны

Инструменты создаются на текущем `Meter`, поэтому `MeterProvider` c `InMemoryMetricReader` нужно
поставить **до** `build_app`/`Server` — общие фикстуры `metric_reader` / `read_metric` в
`tests/conftest.py` делают это сами:

```python
async def test_metrics(read_metric, log) -> None:
    app = httpkit.build_app(httpkit.Config(), log)
    ...
    assert read_metric("http_requests_total", {"path": "/users/{uid}", "status": "200"}) == 1
```

Имя спана и атрибуты проверяются фикстурой `span_exporter` (`InMemorySpanExporter`): запрос к
`/users/7` даёт спан `GET /users/{uid}` с `http.route` и `request_id`.

## Идемпотентность

`httpkit.MemoryStore()` — in-memory `IdempotencyStore`: первый write-запрос вызывает обработчик,
повтор с тем же ключом, телом, query и клиентом отдаёт сохранённый ответ; другое тело или query →
`422`. `MemoryStore(clock=Tickable(start))` двигает TTL без реального ожидания. Свой store
подставляется заглушкой с нужной ошибкой — так проверяются fail-open, `fail_closed` и снятие резерва
после ошибки `set`.

## Проводка целиком

Поднимите `HttpProvider` на свободном порту (`HTTP_ADDR=127.0.0.1:0`) и ходите по HTTP — так
проверяется, что пробы из `healthkit.Probes` доезжают до `/ready`, а ошибка обработчика не утекает
клиенту:

```python
container = make_async_container(httpkit.HttpProvider(), AppProvider())
server = await container.get(httpkit.Server)
async with httpx.AsyncClient(base_url=f"http://{server.addr()}") as c:
    assert (await c.get("/ready")).status_code == 503
await container.close()
```

Занятый порт валит именно создание `Server` (`ListenError`) — тест на это держит сокет открытым сам.
