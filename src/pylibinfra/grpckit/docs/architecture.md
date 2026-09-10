# Архитектура grpckit

`grpckit` владеет транспортом (HTTP/2 на `GRPC_ADDR` и/или UDS), цепочкой
интерсепторов, наблюдаемостью и lifecycle. Доменные gRPC-сервисы (сгенерированные
стабы плюс бизнес-логика) живут в сервис-репо и подключаются как данные.

## Компоненты

| Тип | Файл | Роль |
|---|---|---|
| `Config`, `ConfigError`, `new_config` | `config.py` | Публичный env-контракт сервера и его проверка. |
| `Server` | `server.py` | Обёртка над `grpc.aio.Server` + health: `listen`/`serve`/`stop`/`addr`/`grpc`/`set_serving`. |
| `ServiceRegistrar`, `Services` | `server.py` | Порт регистрации доменного сервиса. |
| `build_interceptors` | `server.py` | Фиксированный порядок цепочки. |
| `HandlerInterceptor` и потомки | `interceptors.py` | request-id, access-log с метриками, recovery. |
| `Authenticator`, `AuthInterceptor`, `identity` | `auth.py` | Порт аутентификации и его интерсептор. |
| `Health` | `health.py` | `grpc.health.v1` + цикл переоценки проб. |
| `Instruments` | `metrics.py` | RED-метрики сервера. |
| `is_server_fault` | `status.py` | Классификация кодов для уровня access-log. |
| `GrpcProvider` | `module.py` | dishka-сборка: `Config` + `Services` + `Probes` + опц. `Authenticator` → `Server` с lifecycle. |

## Поток сборки

`Server(cfg, log, ...)` строит `grpc.aio.Server`, но **не** занимает адрес — это
делает `listen()` (и `serve()`, который зовёт его сам):

1. **Инструменты** — счётчики и гистограмма кита создаются в конструкторе, чтобы
   тест с новым `MeterProvider` их видел.
2. **Опции транспорта** — лимиты сообщений, keepalive, `max_concurrent_streams`.
   Опции консьюмера идут **первыми**: при дубле аргумента gRPC берёт первое
   вхождение, поэтому одноимённая опция консьюмера перекрывает опцию кита.
3. **Цепочка интерсепторов** — фиксированный порядок (ниже), интерсепторы
   консьюмера в хвост.
4. **Health-сервис** регистрируется всегда.
5. **Доменные сервисы** из `services` применяются в порядке кортежа.
6. **Reflection** — только при `GRPC_REFLECTION=true`.
7. **TLS** — при заданной паре `GRPC_TLS_CERT_FILE`/`GRPC_TLS_KEY_FILE` креды
   читаются в `listen()`; нечитаемый файл — `ConfigError`.

`listen()` занимает адреса синхронно: занятый порт валит старт приложения
(`ListenError`), а не первый RPC. Голый `:9090` (нотация golibinfra) grpc-python
не принимает — кит подставляет хост «все интерфейсы» сам. Отказ TCP-bind после
успешного UDS-bind снимает уже созданный сокет: иначе остался бы файл, к которому
никто не слушает.

## Цепочка интерсепторов

Порядок фиксирован в `build_interceptors` и одинаков для unary и stream:

| # | Интерсептор | Назначение |
|---|---|---|
| 1 | otel-grpc | Server-span, извлечение трейс-контекста из metadata. Метрик не эмитит; предикат `trace_filter` снимает спан с выбранных методов. |
| 2 | `RequestIDInterceptor` | Берёт `x-request-id` из metadata; невалидный (пустой, длиннее 128, не печатный ASCII) заменяет на UUID v7. Кладёт id в общий с `httpkit` contextvar и эхом отдаёт клиенту. |
| 3 | `AccessLogInterceptor` | Меряет вызов, пишет одну запись с уровнем по gRPC-коду и одну точку в RED-метрики. |
| 4 | `RecoveryInterceptor` | Необработанное исключение → `grpc_server_panics_total`, лог со стеком, `INTERNAL "internal error"` без деталей. Намеренный статус (`context.abort`) и отмена проходят как есть. |
| 5 | `AuthInterceptor` (опц.) | Только при заданном `Authenticator`. Bearer-токен → identity в контексте вызова. Health и reflection — exempt. |
| 6 | интерсепторы консьюмера | Вплотную к handler'у: видят request-id и identity, их исключения ловит recovery, их исход попадает в access-log. |

WHY такого порядка:

- **request-id первым** — id есть у всех остальных, включая access-log и лог
  необработанного исключения: без него самый важный класс инцидентов не
  коррелируется с вызовом клиента.
- **access-log снаружи recovery** — исключение превращается в `INTERNAL` до
  возврата в access-log, поэтому у отказа есть и запись `grpc call failed` с
  кодом, и отдельная `grpc panic recovered` со стеком.
- **recovery снаружи auth и интерсепторов консьюмера** — их код тоже
  потребительский и тоже может упасть.
- **auth после access-log** — отклонённые вызовы попадают в лог с корректным
  request-id.

### Как писать свой интерсептор

`grpc.aio` разбирает цепочку **до** выполнения RPC: тело `intercept_service`
отрабатывает на разборе handler'а, когда request-id и identity ещё не привязаны.
Читать их нужно в обёртке поведения — для этого кит экспортирует
`HandlerInterceptor`: подкласс переопределяет `call_unary`/`call_stream`, а форму
handler'а (unary/stream, клиентский стрим) база сохраняет сама.

```python
class TenantInterceptor(HandlerInterceptor):
    async def call_unary(self, inner, method, request, context):
        tenant = resolve(identity())  # identity уже привязана
        return await inner(request, context)
```

## Формы стримингового handler'а

grpc.aio принимает две формы handler'а с потоковым ответом: async-генератор
(`yield`) и reader/writer (`await context.write(...)`). `HandlerInterceptor`
сохраняет обе, но otel-grpc-instrumentation снаружи цепочки разбирает только
генераторы — **пишите стриминговые handler'ы как async-генераторы**. Стандартный
`grpc.health.v1` кит поднимает своей реализацией `Watch` в форме генератора
именно поэтому.

## Health

Health-сервис поднят всегда. Overall (`""`) переключается автоматически: `SERVING`
на `serve`, `NOT_SERVING` на `stop` (до закрытия портов, чтобы оркестратор успел
увести трафик). При непустом кортеже `healthkit.Probes` и `GRPC_HEALTH_INTERVAL > 0`
overall переоценивается по пробам: они гоняются **параллельно**, каждая под
`healthkit.with_timeout(GRPC_HEALTH_PROBE_TIMEOUT)`; любая упавшая даёт
`NOT_SERVING`. Логируется смена статуса, а не каждый тик.

## Lifecycle (dishka)

`GrpcProvider` отдаёт `Server` async-generator-провайдом: `serve()` до `yield`,
`stop()` после. dishka строит ресурсы лениво, поэтому приложение обязано
резолвить `Server` на старте — иначе порт не занят и RPC никто не обслуживает
(в golibinfra ту же роль играет `fx.Invoke` внутри модуля).

`Config` Provider не отдаёт (asymmetry note): его регистрирует консьюмер, дешёвый
режим — `new_config()`. `Services`, `ServiceNames`, `healthkit.Probes` и
`Authenticator` имеют пустые дефолты — консьюмер перекрывает их своим Provider'ом,
зарегистрированным ПОСЛЕ `GrpcProvider`.

`Server` одноразовый: `stop()` переводит health-сервис в необратимый
graceful-shutdown (`enter_graceful_shutdown` стандартного `grpc.health.v1`),
поэтому перезапуск делается новым объектом, а не повторным `serve()`.

## Ключевые решения (WHY)

- **Собственные RED-метрики.** В golibinfra их даёт otelgrpc StatsHandler
  (`rpc.server.*`); Python-инструментация серверных метрик не эмитит вовсе,
  поэтому кит считает их сам — иначе gRPC-сервис остался бы без RED-сигнала.
- **request-id в общем contextvar `_internal.requestid`** — единый `request_id` в
  логах HTTP и gRPC без импорта `httpkit`; `logkit` добавляет поле в каждую
  запись сам. Входящий id валидируется, потому что попадает в каждую строку лога.
- **Эхо request-id после handler'а.** `send_initial_metadata` в grpc.aio
  отправляет метаданные немедленно и ровно один раз: отправь кит их первым — и
  handler, которому нужны свои initial metadata, получил бы ошибку. Поэтому эхо
  уходит после handler'а (для стрима — перед первым сообщением), а отказы,
  которые формирует сам кит (auth, recovery), отправляют его перед `abort`.
- **Обезличенный `UNAUTHENTICATED`** — текст ошибки порта может нести внутренние
  детали (имя ключа, причина истечения); клиенту это знать не нужно, оператору —
  да, поэтому деталь идёт в лог.
- **Статус вызова задаётся только `context.abort`.** Он поднимает `AbortError`,
  который не наследует `grpc.RpcError`, поэтому recovery надёжно отличает
  намеренный отказ от аварии. Любое другое исключение — авария, включая
  `AioRpcError` сорвавшегося исходящего вызова: пропусти кит его наружу, grpc
  отдал бы клиенту текст чужого сервиса под `UNKNOWN`.
- **Уровень access-log по коду** — `error` только там, где виноват сервер
  (`INTERNAL`/`UNKNOWN`/`DATA_LOSS`/`UNAVAILABLE`/`UNIMPLEMENTED`), остальные
  не-OK коды — `warn`, чтобы алерты на error не шумели от невалидных запросов.
- **Health по пробам, а не по факту старта** — иначе оркестратор видит SERVING у
  сервиса с недоступной БД. Пробы берутся из того же кортежа `healthkit.Probes`,
  что читает `/ready` в `httpkit`.
- **Auth — порт, а не реализация**: кит владеет механикой интерсептора, но не
  знает про JWT и сессии; проверку токена отдаёт домену.
- **UDS — расширение поверх golibinfra.** Co-located sidecar ходит по
  unix-сокету без сетевого хопа; TCP при этом остаётся включённым, если
  `GRPC_ADDR` не пуст.
