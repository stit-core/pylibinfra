# httpkit — архитектура

## Два слоя: FastAPI и uvicorn

`Server` несёт оба слоя:

- **`Server.app`** (`FastAPI`) — операции с валидацией ввода/вывода и авто-генерацией OpenAPI;
  на нём консьюмер регистрирует роуты и dishka-инъекцию (`httpkit.setup`).
- **uvicorn** — транспорт: `listen()` занимает порт, `serve()` обслуживает соединения,
  `stop()` гасит их в пределах `HTTP_SHUTDOWN_TIMEOUT`.

`build_app(cfg, log, ...)` собирает то же приложение без транспорта — для консьюмера, который
сервит сам (ASGI-хост, тесты, встроенный webhook-runner).

## Старт и остановка

`listen()` занимает порт синхронно и идемпотентно: занятый адрес — `ListenError`, а не строка лога
на уже «запущенном» графе. `HttpProvider` вызывает его при создании `Server`, пишет фактический
адрес (`http server listening`, важно при `HTTP_ADDR=:0`) и запускает `serve()` фоновой задачей;
её падение попадает в лог как `http server stopped` (Error) и всплывает исключением при закрытии
контейнера. `addr()` отдаёт фактический `host:port` (`[::]:port` при bind-all — сокет dual-stack,
как `net.Listen("tcp", ":port")` в Go; без IPv6 в системе — `0.0.0.0:port`). Остановленный сервер
порт заново не занимает.

## Раскладка цепочки на `add_middleware`

Starlette строит стек как `ServerErrorMiddleware → user middleware → ExceptionMiddleware → router`,
а `app.add_middleware` вставляет middleware **в начало** списка: последний добавленный оказывается
самым внешним. `build_app` добавляет цепочку с хвоста, поэтому порядок выполнения запроса читается
сверху вниз, а ответа — снизу вверх:

| # | Middleware | Условие | Зачем именно здесь |
|---|---|---|---|
| 1 | `RequestID` | всегда | request-id в contextvar до всего остального: он попадает в каждую строку лога и в атрибут спана |
| 2 | `OTel` | всегда | server-span и входящий `traceparent`; всё ниже работает внутри спана, поэтому `trace_id` есть в access-log и panic-log |
| 3 | `AccessLog` | всегда | строка на запрос с `route`, `status`, `bytes`, `duration_ms` |
| 4 | `Metrics` | всегда | RED на шаблоне маршрута; ниже него — всё, что может ответить клиенту |
| 5 | `Recovery` | всегда | ловит исключение из всего, что ниже: 500 + `http_panics_total` |
| 6 | `SecurityHeaders` | всегда | заголовки на любой ответ, включая 429 и 413 |
| 7 | `CORS` | если `CORS.Origins` не пуст | preflight отвечает 204 до лимитера и тела |
| 8 | `RateLimit` | если `RateLimit.RPS > 0` | per-IP bucket; дешёвый отказ до тяжёлой работы |
| 9 | `MaxBody` | если `MaxBody.MaxBytes > 0` | лимит тела до чтения обработчиком |
| 10 | `Idempotency` | если в контейнере есть `IdempotencyStore` | дедуп write-запросов по `Idempotency-Key` |
| 11 | `Auth` | если в контейнере есть `AuthExtractor` | identity в `request.state.auth` |
| 12 | middleware консьюмера | `httpkit.Middlewares` | ближе всего к обработчику |

Почему так: `RequestID` и `OTel` первыми, чтобы контекст запроса был полным у всех остальных.
`AccessLog` и `Metrics` — **над** `Recovery`, `CORS` и `RateLimit`: иначе запросы с исключением
(самые тяжёлые 500-е), 429 и preflight отвечали бы выше них и не попадали ни в
`http_requests_total`, ни в access-log. Обе записи сделаны в `finally`, поэтому запрос учитывается
и когда снизу прилетело исключение. Дальше — от дешёвого отказа к дорогой работе: заголовки,
preflight, лимитер, лимит тела, идемпотентность, аутентификация.

Ошибки, объявленные обработчиком (`APIError`, `HTTPException`, ошибки валидации), рендерит
`ExceptionMiddleware` — он **внутри** всей цепочки, поэтому такие ответы проходят через `Metrics`,
`AccessLog` и получают заголовки кита. Незамапленное исключение через него проходит насквозь и
достаётся `Recovery`.

## Обёртка ответа

Все middleware работают с одной обёрткой `send` на запрос (`ResponseTap` в scope): она считает
статус и объём тела, игнорирует повторный `http.response.start` и применяет заголовки,
зарегистрированные китом на пути запроса (`X-Request-ID`, security-заголовки, CORS, `Retry-After`).
Это ASGI-эквивалент «поставить заголовок в `w.Header()` до вызова next»: заголовки получает любой
ответ, кем бы он ни был написан — обработчиком, `ExceptionMiddleware`, лимитером или `Recovery`.

`Idempotency` добавляет к ней собственный буфер тела ответа с лимитом `MaxBodyBytes`.

## Точки расширения

- `Server(..., middlewares=(Middleware(cls, **kwargs), ...))` / DI-ключ `httpkit.Middlewares` —
  ASGI-middleware консьюмера; они встают **внутрь** цепочки кита, поэтому их исключения ловит
  `Recovery`, а их ответы попадают в метрики и access-log.
- `Server(..., security_schemes={...})` / DI-ключ `OptionalSecuritySchemes` —
  `components.securitySchemes` OpenAPI; дефолтных схем кит не выставляет.
- `Server(..., lifespan=httpkit.dishka_lifespan(container))` — закрытие контейнера после того, как
  сервер отдал все ответы.
- `HttpProvider(autostart=False)` / `Server(..., autostart=False)` — собрать приложение, но не
  занимать порт: сервит его сам консьюмер (бот с вебхуком, встроенный ASGI-хост).
- `HTTP_DOCS_ENABLED=false` — сервис без `/docs`, `/redoc` и `/openapi.json`.

## Зависимости через dishka

- `httpkit.Config` — регистрирует caller (`new_config()` или свой App-Provider).
- `logkit.Logger` — `Recovery`, `AccessLog`, `Auth`, `/ready`, обработчики ошибок; обязателен в контейнере (`logkit.LogkitProvider`), дефолта у кита нет.
- `OptionalStore` (`IdempotencyStore | None`) — есть в графе → `Idempotency` с `Config.Idempotency`.
- `OptionalAuth` (`AuthExtractor | None`) → `Auth` и scope идемпотентности; нет — гейт не ставится.
- `healthkit.Probes` → `/ready`; `httpkit.Probe` — алиас `healthkit.Probe`, его же возвращают
  `pgkit`, `natskit`, `cachekit`, `storagekit`.
- `Middlewares`, `OptionalSecuritySchemes` — точки расширения выше.

Каждая опциональная зависимость имеет дефолт в `HttpProvider`; консьюмер регистрирует свой Provider
**после** него и перекрывает нужное.

## RequestID

Принимает `X-Request-ID` клиента только печатным ASCII без пробелов длиной до 128 байт (id попадает
в каждую строку лога), иначе генерирует UUIDv7 (`idkit.new_v7`). Хранилище — общий contextvar
`_internal.requestid`: тот же, что читает `httpclient` (прокидывает заголовок дальше) и пишет
`grpckit`. Поле `request_id` в записи лога добавляет `logkit` сам — bind'ить его в транспорте не нужно.

## Error-модель

`APIError` — problem-объект `{type, title, status, detail, instance, code}` (пустые поля
опускаются), он же исключение: из любого слоя уходит с `Content-Type: application/problem+json`.

- `error(code, status, detail)`, хелперы `bad_request` … `internal`, `unavailable`.
- `map_err(err, *mappings)` — таблица `ErrorMapping(match, code, status, detail)`.
- `map_is(err, target, code, status, detail)` — совпадение по типу или sentinel-инстансу.
- `map_as(err, exc_type, fn)` — по конкретному типу, с построением ответа из самой ошибки;
  `Exception`/`BaseException` запрещены (совпали бы с любой ошибкой).

`map_is` и `map_as` идут по цепочке `raise ... from ...` — обёрнутая причина тоже совпадает.

Незамапленная ошибка обработчика **не** уходит клиенту: `register_error_handlers` нейтрализует
любой 5xx до `{"code":"INTERNAL","status":500,"detail":"internal error"}`, а полный текст пишет в
лог (`handler error`). Нейтрализация не зависит от логгера: без него теряется только текст причины.
Ошибки валидации (422) доходят до клиента вместе с `errors[]` — в них нет данных сервера.

## Аутентификация

`AuthExtractor` — `async (Request) -> identity`. Гейт **deny-by-default**: закрывает все маршруты,
кроме `/health`, `/ready`, docs-путей и `HTTP_QUIET_PATHS`. Публичные операции сервиса объявляет сам
extractor — вернуть анонимную identity для своего списка путей.

Ошибка extractor'а клиенту не уходит: `401 UNAUTHORIZED` (причина — в лог на Debug), возвращённый
`APIError` сохраняет свой статус и код, а ошибка, обёрнутая в `AuthUnavailableError` (JWKS,
session store недоступны), даёт `503 AUTH_UNAVAILABLE` — деградация зависимости не должна
разлогинивать пользователя.

Субъект идемпотентности extractor кладёт через `httpkit.bind_subject(sub)`; `httpkit.subject()`
читает его в обработчике. Identity доступна как `request.state.auth`.

## Идемпотентность

`Idempotency` буферизует тело запроса и ответа, резервирует ключ в `IdempotencyStore` и
воспроизводит сохранённый ответ на повтор. Модель:

- **Fingerprint** — метод + путь + канонизированный query + тело: `?dry_run=true` и «боевой» запрос
  с одним ключом дают `422`, а не чужой ответ.
- **Scope ключа** — аутентифицированный субъект (`bind_subject` в extractor'е), иначе хеш
  `Authorization` + `Cookie`. Субъект устойчив к ротации короткоживущего токена между попытками.
- **Replay — после аутентификации.** Когда в графе есть `AuthExtractor`, middleware вызывает его до
  обращения к store: отозванная сессия с тем же заголовком сохранённый ответ не получает. Успешная
  проверка помечает scope, и `Auth` не верифицирует те же credentials второй раз. Анонимный запрос
  (extractor вернул ошибку) идёт **без дедупликации** (`result=skipped`) — и, если гейт включён,
  дальше получает 401.
- **Запись не зависит от клиента.** Операции store идут под `asyncio.shield` и
  `HTTP_IDEMPOTENCY_STORE_TIMEOUT`: клиент отвалился по таймауту — результат всё равно сохранён, и
  его ретрай получает replay, а не второе выполнение. Отмену несёт только собственный дедлайн:
  зависшая операция снимается по нему, а не живёт фоновой задачей до конца жизни процесса.
- **Резерв снимает только владелец.** `reserve` возвращает токен, `release(key, token)` удаляет
  резерв, лишь пока ключ держит этот токен: запрос, переживший `ReserveTTL`, своим отложенным
  `release` не открывает дорогу третьему запросу параллельно со вторым. Исключение обработчика
  освобождает ключ, иначе ретраи получали бы `409` до конца `ReserveTTL`.
- Ответы `5xx` и ответы больше `MaxBodyBytes` не сохраняются. Ошибка store — fail-open
  (`HTTP_IDEMPOTENCY_FAIL_CLOSED=true` → `503`).

Запись хранится как JSON `{fp, status, header, body}` (тело — base64), поэтому один и тот же store
читают и Python-, и Go-сервисы.

## Health / readiness

`register_health(app, log, probe_timeout, probes)`:

- `GET /health` — liveness, всегда `{"status":"ok"}`.
- `GET /ready` — прогоняет пробы **параллельно** (`asyncio.gather`), каждую под своим таймаутом
  (`HTTP_READY_PROBE_TIMEOUT`): латентность readiness — максимум по пробам, а не сумма. Тело
  перечисляет пробы по имени со статусом `ok`/`down`, а текст ошибки уходит только в лог
  (`readiness probe failed`) — в нём бывают DSN и внутренние адреса.

## SSE и WebSocket

Обёртка ответа не буферизует тело: каждый `http.response.body` уходит наружу сразу, поэтому
стриминг (`StreamingResponse`, SSE) работает через всю цепочку. Не-HTTP-скоупы (`websocket`,
`lifespan`) каждый middleware пропускает без изменений, так что upgrade проходит насквозь; метрик и
access-log-строки у них нет. Идемпотентность стримов не касается: она включается только на
`POST`/`PUT`/`PATCH` с заголовком ключа.
