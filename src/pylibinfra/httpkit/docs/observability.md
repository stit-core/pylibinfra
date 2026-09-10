# httpkit — observability

Сигналы эмитятся через глобальные OTel-провайдеры; без `MeterProvider` / `TracerProvider`
инструменты — no-op. Resource attrs и экспорт настраивает потребитель (`telemetry` или своя
проводка OTel SDK).

## Метрики

Instrumentation-scope `pylibinfra.httpkit`. Гистограмма длительности — `unit="s"` и общие для всех
китов границы `_internal.otelkit.DURATION_BUCKETS`. Инструменты создаются один раз на приложение и
переиспользуются всеми middleware.

| Метрика | Тип | Labels | Эмитится | Назначение |
|---|---|---|---|---|
| `http_requests_total` | Counter | `method`, `path`, `status` | `Metrics` | счётчик запросов |
| `http_request_duration_seconds` | Histogram | `method`, `path`, `status` | `Metrics` | длительность обработки |
| `http_panics_total` | Counter | `path` | `Recovery` | восстановленные исключения |
| `http_max_body_rejections_total` | Counter | `path` | `MaxBody` | ответы 413 |
| `http_rate_limited_total` | Counter | — | `RateLimit` | ответы 429 |
| `http_idempotency_total` | Counter | `result` | `Idempotency` | исходы: `stored`, `replayed`, `conflict`, `in_progress`, `skipped`, `error` |

Label `path` везде — шаблон маршрута (`/users/{uid}`); запрос без маршрута получает `unmatched`, так
что сканы 404 не растят кардинальность. Label `method` — только стандартные методы (`GET`, `HEAD`,
`POST`, `PUT`, `PATCH`, `DELETE`, `OPTIONS`, `CONNECT`, `TRACE`), любой другой токен клиента
становится `other`.

Запросы с исключением, 429 и preflight 204 попадают в `http_requests_total` наравне с остальными:
`Metrics` стоит выше `Recovery`, `CORS` и `RateLimit` и пишет сэмпл в `finally`. У 429 и preflight
`path` — `unmatched`: их отвергли до роутинга, и шаблон ещё не известен.

## Спаны

`OTel` стоит вторым в цепочке (после `RequestID`): открывает server-span и извлекает входящий
`traceparent`, а все последующие middleware, включая `AccessLog` и `Recovery`, работают внутри
спана. Имя спана — `METHOD /route/{param}`: после ответа, когда шаблон маршрута известен, имя
уточняется (`GET /users/{uid}`); запрос без маршрута оставляет имя `GET`.

Атрибуты спана: `http.request.method`, `url.path`, `http.response.status_code` — ставит `OTel`;
`http.route` и `request_id` — `Metrics`. Ответ `>= 500` помечает спан статусом `ERROR`.

Пути из `HTTP_QUIET_PATHS` (по умолчанию `/health`, `/ready`) спан не открывают: пробы оркестратора
идут каждые несколько секунд и трейсы им не нужны.

## Логи

`AccessLog` пишет строку `http` на запрос: `method`, `path`, `route`, `status`, `bytes`,
`duration_ms` (миллисекунды, float); уровень по статусу (`>=500` error, `>=400` warn, иначе info).
Успешные ответы на `HTTP_QUIET_PATHS` уходят на debug, а их ошибки остаются на warn/error.
`request_id` и `trace_id`/`span_id` добавляет `logkit` из контекста.

Остальные строки кита:

- `panic recovered` (error) с `panic`, `path`, `route` и `stack` — `Recovery`.
- `handler error` (error) со `status`, `code` и полным текстом ошибки — обработчик ошибок; клиенту
  при этом уходит нейтральный 500.
- `auth rejected` (debug) / `auth backend unavailable` (warn) — `Auth`.
- `idempotency auth rejected` (debug), `idempotency store failed` (warn) с `op` — `Idempotency`.
- `readiness probe failed` (warn) с `probe` и `err` — `/ready`; текст ошибки клиенту не отдаётся.
- `http server listening` (info) с фактическим `addr` — старт через `HttpProvider`;
  `http server stopped` (error) — падение `serve`.

Собственные сообщения uvicorn (ошибки соединений, TLS-handshake) идут через stdlib `logging`;
чтобы они попали в единственный sink сервиса, консьюмер вызывает `logkit.capture_stdlib(log)`.
Access-log uvicorn выключен — строку запроса пишет `AccessLog` кита.
