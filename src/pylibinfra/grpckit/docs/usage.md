# Использование grpckit

## Регистрация сервиса

Доменный сервис отдаёт `ServiceRegistrar` — функцию, вешающую servicer на сервер;
консьюмер складывает их в кортеж `Services`:

```python
from pylibinfra.grpckit import Services

from myservice import greeter_pb2_grpc


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def services(self, greeter: GreeterServicer) -> Services:
        def register(server):
            greeter_pb2_grpc.add_GreeterServicer_to_server(greeter, server)

        return (register,)
```

`AppProvider` регистрируется **после** `GrpcProvider` — он перекрывает пустой
дефолт `Services`. Порядок применения регистраторов = порядок в кортеже. Кит не
импортирует ни одного `*_pb2_grpc` — граница «инфра-lib ↔ домен» остаётся целой.

Поздняя регистрация (интероп, динамические сервисы) — через `server.grpc()`.

## Authenticator

Auth опционален: реализуйте порт в сервис-репо и положите его в контейнер.

```python
class AccessDomain:
    async def authenticate(self, token: str) -> Session:
        return await self._sessions.resolve(token)  # ошибка = отказ


class AuthProvider(Provider):
    scope = Scope.APP

    @provide
    def authenticator(self) -> OptionalAuthenticator:
        return AccessDomain(...)
```

Контракт:

- Токен берётся из metadata `authorization`; префикс `Bearer ` снимается
  (регистр любой), голый токен тоже принимается.
- Пустой токен (в том числе заголовок из одного префикса) → `UNAUTHENTICATED`
  «missing bearer token» **до** вызова порта.
- Любое исключение порта → `UNAUTHENTICATED` «unauthenticated»; текст ошибки
  уходит только в warn-лог `grpc auth rejected`.
- Успех → возвращённое значение доступно handler'у как `grpckit.identity()` и
  снимается по завершении вызова.
- Health (`/grpc.health.v1.Health/`) и reflection (`/grpc.reflection.`) — exempt.

Вне DI: `Server(cfg, log, authenticator=AccessDomain(...))`.

## Request-id

Клиент может прислать metadata `x-request-id` (печатный ASCII, до 128 символов) —
сервер сохранит его, иначе сгенерирует UUID v7. Handler читает его через
`grpckit.request_id()`, `logkit` кладёт в каждую запись поле `request_id`, клиент
получает эхо в initial metadata. Ключ общий с `httpkit`, поэтому HTTP→gRPC hop
сохраняет один идентификатор.

Handler, которому нужны собственные initial metadata, отправляет их сам
(`await context.send_initial_metadata(...)`) — тогда эхо кита не добавляется;
положите `request_id()` в свои метаданные, если он нужен клиенту.

## Health-статус

Health поднят всегда: overall `SERVING` после `serve()`, `NOT_SERVING` на `stop()`.
Per-service статус — вручную:

```python
await server.set_serving("greeter.Greeter", True)
await server.set_serving("greeter.Greeter", False)  # потеряли зависимость
```

Автоматическая переоценка — по кортежу `healthkit.Probes` из контейнера:

```python
class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def probes(self, db: DB, cache: Cache) -> Probes:
        return (pgkit.probe(db), cachekit.probe(cache))
```

Каждые `GRPC_HEALTH_INTERVAL` пробы гоняются параллельно с дедлайном
`GRPC_HEALTH_PROBE_TIMEOUT`; упавшая переводит overall в `NOT_SERVING`.
`GRPC_HEALTH_INTERVAL=0` выключает переоценку — тогда статус меняет только
`set_serving`.

## Свои интерсепторы

```python
from pylibinfra.grpckit import HandlerInterceptor, identity


class TenantInterceptor(HandlerInterceptor):
    """Кладёт тенанта вызова в контекст: identity уже привязана."""

    async def call_unary(self, inner, method, request, context):
        with tenant_scope(resolve(identity())):
            return await inner(request, context)


server = Server(cfg, log, services=services, interceptors=(TenantInterceptor(),))
```

Интерсепторы консьюмера идут в хвост цепочки: они видят request-id и identity,
их исключения ловит recovery, их исход попадает в access-log. Читать контекст
вызова в теле `intercept_service` нельзя — цепочка разбирается до выполнения RPC.

`interceptors`, `server_options` и `trace_filter` — аргументы конструктора, а не
DI-ключи (как `WithUnaryInterceptors`/`WithServerOptions` в golibinfra): нужен
свой интерсептор под dishka — соберите `Server` в своём Provider'е вместо
`GrpcProvider`.

Свои опции транспорта — `server_options`; они идут первыми, поэтому одноимённая
опция перекрывает опцию кита:

```python
Server(cfg, log, server_options=(("grpc.max_concurrent_streams", 128),))
```

## Трассировка отдельных методов

`trace_filter` — предикат «трассировать ли вызов» (аналог `otelgrpc.WithFilter`).
Типичный случай — не заводить спан на каждую health-пробу оркестратора:

```python
Server(
    cfg,
    log,
    trace_filter=lambda details: not str(details.method).startswith("/grpc.health.v1.Health/"),
)
```

Готовые предикаты и комбинаторы — в `opentelemetry.instrumentation.grpc.filters`.
Фильтр снимает только спан; access-log и метрики вызова пишутся как обычно.

## Отказ из handler'а

Намеренный статус — `context.abort(code, details)`: его текст уходит клиенту как
есть и попадает в access-log с этим же кодом.

```python
await context.abort(grpc.StatusCode.NOT_FOUND, "order not found")
```

Всё, что вылетает из handler'а исключением, кит считает аварией и гасит в
`INTERNAL: internal error` — включая `AioRpcError` сорвавшегося исходящего
вызова: его `details` описывают чужой сервис и клиенту не предназначены. Хотите
пробросить код downstream'а — сделайте это явно:

```python
try:
    await stub.GetOrder(request)
except grpc.aio.AioRpcError as exc:
    if exc.code() is grpc.StatusCode.NOT_FOUND:
        await context.abort(grpc.StatusCode.NOT_FOUND, "order not found")
    raise
```

## Стриминговые handler'ы

Пишите их как async-генераторы:

```python
async def Subscribe(self, request, context):
    async for event in self._events():
        yield event
```

Форму reader/writer (`await context.write(...)`) кит проводит через свою цепочку,
но otel-grpc-instrumentation снаружи её не разбирает — вызов завершится ошибкой.

## Прямой запуск (без DI)

```python
server = Server(new_config(), log, services=(register_greeter,))
server.listen()  # занять порт до старта фоновых задач
await server.serve()
try:
    await server.grpc().wait_for_termination()
finally:
    await server.stop()
```

`Server.addr()` после `listen()` отдаёт фактический адрес — он нужен, когда порт
выбирает ОС (`GRPC_ADDR=127.0.0.1:0`).

## Reflection

Reflection выключен по умолчанию. Для dev-отладки `grpcurl`'ом установите в
сервисе `grpcio-reflection`, включите `GRPC_REFLECTION=true` и передайте имена
сервисов — grpc-python не собирает их с сервера сам:

```python
class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def service_names(self) -> ServiceNames:
        return ServiceNames(("greeter.Greeter",))  # health и reflection кит добавит сам
```

Без DI это аргумент `Server(..., service_names=("greeter.Greeter",))`. Пустой
кортеж — reflection покажет только health и себя. В prod держим выключенным:
reflection раскрывает схему API и exempt от auth.
