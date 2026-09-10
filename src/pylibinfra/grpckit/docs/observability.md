# Observability grpckit

Единый источник правды по сигналам кита. Метрики кит считает сам, спаны даёт
otel-grpc-instrumentation, access-log и записи health-loop пишет `logkit`.

## Метрики

Инструменты создаются на meter'е `pylibinfra.grpckit` (`otelkit.meter`) в
конструкторе `Server`; без `MeterProvider` — no-op.

| Метрика | Тип | Labels | Что |
|---|---|---|---|
| `grpc_server_requests_total` | counter | `grpc_method` (полный `/package.Service/Method`), `grpc_code` (имя кода: `OK`, `NOT_FOUND`, …) | Завершённые RPC по исходу |
| `grpc_server_request_duration_seconds` | histogram (s, общие бакеты `0.005 … 30`) | `grpc_method`, `grpc_code` | Длительность RPC от входа в цепочку до завершения |
| `grpc_server_panics_total` | counter | `grpc_method` | Необработанные исключения handler'а, погашенные recovery |

Стриминговый вызов считается один раз — при закрытии стрима; длительность
включает всё время стрима.

> **Отличие от golibinfra.** В Go те же сигналы даёт otelgrpc StatsHandler
> (`rpc.server.call.duration`). Python-инструментация серверных метрик не эмитит
> вовсе, поэтому кит считает RED сам — иначе gRPC-сервис остался бы без метрик.
> Пересмотр — если otel-grpc-instrumentation начнёт эмитить `rpc.server.*`.

## Спаны

Спан ставит `aio_server_interceptor` из `opentelemetry-instrumentation-grpc`,
первым в цепочке:

| Сигнал | Деталь |
|---|---|
| Server span | SpanKind `SERVER`, имя — полный метод (`/package.Service/Method`) |
| Атрибуты | `rpc.system` = `grpc`, `rpc.service` (`package.Service`), `rpc.method` (короткое имя), `rpc.grpc.status_code` (числовой код), `rpc.user_agent` |
| Trace-propagation | Входящий трейс-контекст извлекается из metadata; спан становится дочерним к вызывающему |

Метрик otel-grpc не эмитит — RED-сигнал сервера целиком из таблицы выше.

Трассировку отдельных методов (шумные health-пробы оркестратора) снимает
предикат `trace_filter` — аналог `otelgrpc.WithFilter` в golibinfra:

```python
Server(
    cfg,
    log,
    trace_filter=lambda details: not str(details.method).startswith("/grpc.health.v1.Health/"),
)
```

Он снимает **только спан**: access-log и RED-метрики отфильтрованного вызова
пишутся как обычно. Других опций у серверного интерсептора otel-grpc нет:
событий на каждое сообщение (`otelgrpc.WithMessageEvents` в golibinfra) он не
умеет, а provider'ы берутся глобальные — их настраивает OTel-SDK консьюмера.

Корреляция логов с трейсом — через `logkit` (`trace_id`/`span_id` в записи) плюс
`request_id`.

## Request-id

Интерсептор берёт `x-request-id` из входящих metadata; невалидный (пустой, длиннее
128 символов, не печатный ASCII) заменяет на UUID v7. Итоговый id лежит в общем с
`httpkit` contextvar (`_internal.requestid`), поэтому `logkit` добавляет поле
`request_id` в каждую запись сервиса, а `httpclient` продолжает ту же цепочку в
исходящих вызовах. В коде id читается через `grpckit.request_id()`.

Клиенту id возвращается в initial metadata (`x-request-id`) — после выполнения
handler'а, а для стрима перед первым сообщением. Если handler отправил свои
initial metadata сам, эхо не добавляется: вызов важнее заголовка. Отказы, которые
формирует сам кит (auth, recovery), отправляют эхо перед `abort`, поэтому у
ошибочного ответа id есть всегда.

## Access-log

Одна запись на вызов, уровень — по gRPC-коду:

| Исход | Уровень | Сообщение | Поля |
|---|---|---|---|
| `OK` | info | `grpc call` | `grpc_method`, `grpc_code`, `duration_ms` |
| `INTERNAL`, `UNKNOWN`, `DATA_LOSS`, `UNAVAILABLE`, `UNIMPLEMENTED` | error | `grpc call failed` | + `error` |
| остальные не-OK (`INVALID_ARGUMENT`, `NOT_FOUND`, `UNAUTHENTICATED`, `PERMISSION_DENIED`, `CANCELLED`, `DEADLINE_EXCEEDED`, `RESOURCE_EXHAUSTED`, …) | warn | `grpc call failed` | + `error` |

`grpc_code` берётся из статуса вызова, поэтому в записи стоит ровно тот код,
который получил клиент; отмена — `CANCELLED`, отказ без статуса — `UNKNOWN`.
Необработанное исключение попадает в access-log как `INTERNAL` и дополнительно
логируется записью error `grpc panic recovered` (`grpc_method`, `error`, `stack`)
— это касается и `grpc.RpcError`/`AioRpcError` от сорвавшегося исходящего вызова:
его текст несёт детали чужого сервиса и наружу не уходит. Отклонённая
аутентификация пишет warn `grpc auth rejected` (`grpc_method`, `error` — текст
ошибки порта), клиенту уходит только `UNAUTHENTICATED: unauthenticated`.

Health-вызовы оркестратора идут в access-log наравне с остальными; чтобы убрать
их шум, отфильтруйте записи на стороне сборщика логов (спаны снимает
`trace_filter`, см. выше).

Метка `grpc_method` — полное имя зарегистрированного метода, поэтому множество
значений конечно: вызов несуществующего метода до цепочки кита не доходит (grpc
отвечает `UNIMPLEMENTED` сам) и ни метрики, ни записи не порождает.

## Health-loop

При непустом кортеже `healthkit.Probes` и `GRPC_HEALTH_INTERVAL > 0` кит
переоценивает пробы и логирует **смену** overall-статуса (не каждый тик):

| Событие | Уровень | Сообщение | Поля |
|---|---|---|---|
| overall стал `NOT_SERVING` | warn | `grpckit: health not serving` | `probe`, `error` |
| overall вернулся в `SERVING` | info | `grpckit: health serving` | — |

Пробы гоняются параллельно, каждая под `healthkit.with_timeout`
(`GRPC_HEALTH_PROBE_TIMEOUT`). Зависшая зависимость видна в поле `error` как
`probe … timed out after …`.

## Логи транспорта

grpc-python пишет свои сообщения (GOAWAY, keepalive, резолвер) через stdlib
`logging` в логгер `grpc`. Их подхватывает `logkit`: `capture_stdlib(log, cfg)`
уводит корневой `logging` в `Logger`, и `LogkitProvider` зовёт его сам — записи
транспорта выходят тем же JSON и с теми же полями, что остальной лог сервиса.
Отдельного моста `grpclog → logkit` кит не держит: подключать в grpckit нечего.

## Сводка

`grpckit` эмитит `grpc_server_requests_total`,
`grpc_server_request_duration_seconds`, `grpc_server_panics_total` и server span
otel-grpc-instrumentation.
