# grpckit

Серверный gRPC-примитив на `grpc.aio` — то же, что `httpkit` для FastAPI:
`Server` с фиксированной цепочкой интерсепторов (request-id, общий с `httpkit`;
access-log с уровнем по gRPC-коду; recovery, гасящий необработанное исключение в
`INTERNAL` без деталей; опц. auth через порт `Authenticator`), спанами
otel-grpc-instrumentation, RED-метриками и стандартным `grpc.health.v1` с
переоценкой по пробам `healthkit`.

Доменные сервисы подключаются данными: консьюмер отдаёт кортеж `Services` с
регистраторами своих servicer'ов — кит не импортирует ни одного `*_pb2_grpc`.
Транспортная зависимость (`grpcio`) едет в extra `[grpckit]`.

## Импорт

```python
from pylibinfra.grpckit import GrpcProvider, Server, Services, new_config
```

Версия и установка — [корневой README](../../../README.md).

## Быстрый старт

Консьюмер собирает `Services` из регистраторов своих servicer'ов и регистрирует
`Config`; `GrpcProvider` поднимает сервер и держит его lifecycle:

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.grpckit import Config, GrpcProvider, Server, Services, new_config
from pylibinfra.healthkit import Probes
from pylibinfra.logkit import Logger, LogkitProvider

from myservice import greeter_pb2_grpc, GreeterServicer


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def grpc_config(self) -> Config:
        return new_config()  # GRPC_* из ENV, невалидный — ConfigError

    @provide
    def services(self, greeter: GreeterServicer) -> Services:
        return (lambda server: greeter_pb2_grpc.add_GreeterServicer_to_server(greeter, server),)

    @provide
    def probes(self, db: DB) -> Probes:
        return (pgkit.probe(db),)  # health-loop переоценивает их GRPC_HEALTH_INTERVAL


container = make_async_container(GrpcProvider(), LogkitProvider(), AppProvider())

# Сервер резолвится на старте: dishka строит ресурсы лениво, без запроса порт
# не занимается и RPC никто не обслуживает.
server = await container.get(Server)
await server.grpc().wait_for_termination()
```

Минимальный ENV: ничего — `GRPC_ADDR` по умолчанию `:9090`.

Без DI:

```python
from pylibinfra.grpckit import Server, new_config

server = Server(new_config(), log, services=(register_greeter,))
await server.serve()  # занять порт и начать обслуживать
...
await server.stop()  # health → NOT_SERVING, затем graceful-drain
```

## Документация

- [docs/architecture.md](docs/architecture.md) — устройство: сборка сервера, цепочка интерсепторов, health-loop, ключевые решения (WHY).
- [docs/configuration.md](docs/configuration.md) — справочник `Config`/ENV: bind, лимиты, keepalive, TLS, health, reflection, правила валидации.
- [docs/observability.md](docs/observability.md) — метрики, спаны, request-id, уровни access-log, логи health-loop.
- [docs/usage.md](docs/usage.md) — рецепты: регистрация сервиса, `Authenticator`, свои интерсепторы, health-статус, TLS, reflection.
- [docs/testing.md](docs/testing.md) — `testkit/grpctest`: харнесс UDS/TCP для тестов консьюмеров.

## API

| Символ | Сигнатура | Назначение |
|---|---|---|
| `GrpcProvider` | `Provider` | `Server` + lifecycle (`serve` на получении, `stop` на закрытии контейнера). |
| `new_config` | `() -> Config` | `Config` из ENV с валидацией; `Provider` его не отдаёт. |
| `Server` | `(cfg, log=None, *, services=(), probes=(), authenticator=None, interceptors=(), server_options=(), service_names=(), trace_filter=None)` | Сборка сервера без занятия порта. |
| `Server.listen` / `Server.serve` / `Server.stop` | `() -> None` / `async` / `async` | Занять адреса, начать обслуживать, graceful-остановка. |
| `Server.addr` | `() -> str` | Фактический адрес слушателя (порт `0` в тестах). |
| `Server.grpc` | `() -> grpc.aio.Server` | Нижележащий сервер (поздняя регистрация, интероп, `wait_for_termination`). |
| `Server.set_serving` | `async (service, ok)` | Ручной health-статус сервиса (`OVERALL` — весь сервер). |
| `Services` / `ServiceRegistrar` | `tuple[Callable[[grpc.aio.Server], None], ...]` | Порт регистрации доменных сервисов (DI-ключ). |
| `ServiceNames` | `NewType` над `tuple[str, ...]` | Полные имена сервисов для reflection (DI-ключ); health и reflection кит добавляет сам. |
| `Authenticator` / `OptionalAuthenticator` | `Protocol: async authenticate(token) -> identity` / `Authenticator \| None` | Порт аутентификации (реализует консьюмер) и его DI-ключ с пустым дефолтом. |
| `identity` / `request_id` | `() -> Any` / `() -> str` | Identity и request-id текущего вызова. |
| `HandlerInterceptor` | базовый класс | Свой интерсептор с общей механикой unary/stream. |
| `RequestIDInterceptor`, `AccessLogInterceptor`, `RecoveryInterceptor`, `AuthInterceptor` | `HandlerInterceptor` | Звенья цепочки кита — для сборки сервера вручную. |
| `TraceFilter` | `Callable[[Any], bool]` | Предикат «трассировать ли вызов» (аналог `otelgrpc.WithFilter`). |
| `build_interceptors` | `(log, instruments, *, authenticator=None, extra=(), trace_filter=None) -> list` | Цепочка кита — для сборки сервера вручную. |
| `Health` / `OVERALL` | класс / `""` | Health-сервис с циклом переоценки проб и ключ overall-статуса. |
| `Instruments` / `new_instruments` | dataclass / `() -> Instruments` | RED-инструменты кита (создаются на `Server`). |
| `tcp_target` | `(addr) -> str` | Нотация `:port` golibinfra → target grpc-python. |
| `DEFAULT_ADDR`, `DEFAULT_MAX_MSG_SIZE`, `REQUEST_ID_KEY`, `AUTHORIZATION_KEY`, `INTERNAL_MESSAGE` | константы | Дефолты конфига, metadata-ключи и текст, уходящий вместо необработанного исключения. |
| `ConfigError` / `ListenError` | `ValueError` / `RuntimeError` | Невалидный конфиг; занятый адрес. |

## Инварианты

- **Сервер резолвится явно.** dishka строит ресурсы лениво: без
  `await container.get(Server)` порт не занят и RPC не обслуживаются (аналог
  `fx.Invoke` в golibinfra, которого в dishka нет).
- **Нулевой `Config` безопасен**: лимиты, keepalive, грейс и дедлайн health-пробы
  получают дефолты, сервер слушает `:9090`. Отрицательные значения и половина
  TLS-пары — `ConfigError` на старте.
- **Reflection выключен по умолчанию** (раскрывает схему API и exempt от auth),
  требует пакета `grpcio-reflection`, которого нет в зависимостях pylibinfra, и
  имён сервисов (`ServiceNames`) — grpc-python не собирает их с сервера сам.
- **Health и reflection проходят без auth**; всё остальное — под `Authenticator`,
  если он есть в контейнере.
- **Текст исключения наружу не уходит.** Статус вызова handler задаёт через
  `context.abort(...)`; любое исключение — авария (в том числе `AioRpcError`
  сорвавшегося исходящего вызова): клиент получает `INTERNAL: internal error`,
  текст — только в лог.
- **`Server` одноразовый.** `stop()` уводит health в необратимый
  graceful-shutdown, поэтому перезапуск — новый объект, а не повторный `serve()`.
- **UDS снимается только свой.** Файл сокета удаляется на остановке лишь тогда,
  когда этот сервер его занял: сосед по тому же пути не пострадает.
- **Стриминговый handler — async-генератор.** Форму reader/writer (`context.write`)
  не разбирает otel-grpc-instrumentation, который стоит снаружи цепочки.
