# Тестирование grpckit

## testkit/grpctest — харнесс сервера

`pylibinfra.testkit.grpctest.running_server(*services)` поднимает настоящий
`grpckit.Server` — со всей цепочкой интерсепторов и health — и отдаёт target для
`grpc.aio.insecure_channel`; на выходе из контекста сервер гасится.

```python
from pylibinfra.testkit.grpctest import running_server


async def test_greeter():
    async with (
        running_server(register_greeter) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        client = greeter_pb2_grpc.GreeterStub(channel)
        assert (await client.SayHello(request)).text == "hi"
```

| Параметр | Что |
|---|---|
| `*services` | Регистраторы servicer'ов (тот же `ServiceRegistrar`, что в проде) |
| `probes` | `healthkit.Probes` для health-loop |
| `authenticator` | Порт аутентификации — проверка отказов и identity |
| `interceptors` | Интерсепторы консьюмера в хвост цепочки |
| `log` | `logkit.Logger` (обычно capture-логгер теста) |
| `transport` | `UDS` (по умолчанию, временный сокет) или `TCP` (порт выбирает ОС) |
| `config` | Готовый `Config` — когда нужны свои лимиты, интервалы health и т.п. |

UDS-транспорт по умолчанию: он не занимает порт и не даёт гонок между
параллельными тестами. `new_config(transport)` и `target(cfg, server)` доступны
отдельно, если сервер поднимается вручную.

## Сервис без codegen

Транспорт и интерсепторы проверяются generic-handler'ами: сериализаторы по
умолчанию (`bytes → bytes`), сгенерированные стабы не нужны.

```python
def registrar(server):
    handlers = {"Unary": grpc.unary_unary_rpc_method_handler(echo)}
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("test.Echo", handlers),))
```

## Health в тестах

Health доступен сразу после старта: `HealthStub(channel).Check(...)` вернёт
`SERVING`. Для проверки деградации соберите конфиг с коротким интервалом и
дедлайном пробы:

```python
cfg = Config.model_validate(
    {"addr": "127.0.0.1:0", "health_interval": 0.01, "health_probe_timeout": 0.01}
)
```

`Config.model_validate` (а не `Config(...)`) заполняет поля по именам и не
подмешивает переменные окружения прогона.

## Метрики, спаны и логи

Метрики и спаны читаются общими фикстурами репозитория (`read_metric`,
`span_exporter`); фикстура ставит провайдер **до** создания `Server`, потому что
инструменты создаются в его конструкторе. Уровни и поля access-log проверяются
capture-логгером, переданным в `running_server(log=...)`.

```python
assert (
    read_metric(
        "grpc_server_requests_total",
        {"grpc_method": "/test.Echo/Unary", "grpc_code": "OK"},
    )
    == 1
)
```

## TLS в тестах

Пара cert/key генерируется `openssl` во временном каталоге; тест, которому она
нужна, пропускается там, где бинаря нет. Клиент подключается
`grpc.aio.secure_channel` с `grpc.ssl_target_name_override`.

## Гейт

```bash
uv run ruff format --check . && uv run ruff check .
uv run mypy src
uv run pytest -m "not integration"
```

Тесты кита контейнеров не поднимают: сервер и клиент живут в одном процессе, а
время нигде не ждётся реальными паузами — статусы health опрашиваются
ограниченным числом попыток.
