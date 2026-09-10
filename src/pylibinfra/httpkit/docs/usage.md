# httpkit — рецепты

## API-поверхность (кратко)

- **Сервер**: `Server(cfg, log=None, *, store, auth, probes, middlewares, security_schemes,
  lifespan, instr, autostart)`; методы `listen()`, `addr()`, `serve()`, `stop()`, атрибут `app`.
  Без DI — `build_app(cfg, log, ...)`.
- **Middleware** (ASGI-классы): `RequestIDMiddleware`, `OTelMiddleware`, `AccessLogMiddleware`,
  `MetricsMiddleware`, `RecoveryMiddleware`, `SecurityHeadersMiddleware`, `CORSMiddleware`,
  `RateLimitMiddleware`, `MaxBodyMiddleware`, `IdempotencyMiddleware`, `AuthMiddleware`.
- **Ошибки**: `APIError`, `error`/`bad_request`/`unauthorized`/`forbidden`/`not_found`/`conflict`/
  `internal`/`unavailable`, `map_err`, `map_is`, `map_as`, `ErrorMapping`, `register_error_handlers`,
  `PROBLEM_CONTENT_TYPE`, `code_for_status`; `HttpkitError` — база нетранспортных исключений кита
  (`APIError`, `AuthUnavailableError`, `MaxBodyError`, `ListenError`), ловится одним `except`.
- **Auth**: `AuthExtractor`, `AuthUnavailableError`, `bind_subject`, `subject`, `unbind_subject`;
  `AUTH_DONE_KEY` — ключ scope, которым `Idempotency` помечает уже проверенные credentials, чтобы
  гейт не звал extractor второй раз.
- **Health**: `register_health(app, log, probe_timeout, probes)`, `Probe` (= `healthkit.Probe`),
  `HEALTH_PATH`, `READY_PATH`.
- **Идемпотентность**: `IdempotencyStore`, `MemoryStore`, `IdempotencyConfig`,
  `HEADER_IDEMPOTENT_REPLAYED`.
- **Прочее**: `request_id()`, `HEADER_REQUEST_ID`, `client_ip`, `MaxBodyError`, `ListenError`,
  `RateLimiter`, `new_config`, `ConfigError`, `setup`, `shared_secret_guard`,
  `dishka_lifespan` и его тип `Lifespan` (`(FastAPI) -> AsyncContextManager[None]`) — он
  передаётся в `Server(..., lifespan=...)`.

## Сервер через dishka

```python
container = make_async_container(httpkit.HttpProvider(), AppProvider())
server = await container.get(httpkit.Server)  # порт занят, serve() крутится фоном
httpkit.setup(server.app, container)  # FromDishka[...] в роутах
...
await container.close()  # graceful stop в HTTP_SHUTDOWN_TIMEOUT
```

## Добавить роут

```python
from dishka.integrations.fastapi import FromDishka, inject


@server.app.get("/users/{uid}")
@inject
async def get_user(uid: str, svc: FromDishka[UserService]) -> dict[str, str]:
    try:
        user = await svc.get(uid)
    except UserNotFoundError as exc:
        raise httpkit.not_found("USER_NOT_FOUND", "user not found") from exc
    return {"id": user.id, "name": user.name}
```

## APIError / map_err / map_is / map_as

```python
raise httpkit.bad_request("INVALID_EMAIL", "email is malformed")

mapped = httpkit.map_err(
    err,
    httpkit.ErrorMapping(lambda e: isinstance(e, NotFoundError), "NOT_FOUND", 404, "not found"),
    httpkit.ErrorMapping(lambda e: isinstance(e, ConflictError), "CONFLICT", 409, "already exists"),
)

mapped = httpkit.map_is(err, LockedError, "LOCKED", 403, "account locked")
mapped = httpkit.map_as(
    err, ValidationError, lambda e: httpkit.bad_request("VALIDATION", f"bad {e.field}")
)
if mapped is not None:
    raise mapped
```

Незамапленная ошибка клиенту не показывается: он получает `500
{"code":"INTERNAL","detail":"internal error"}`, а полный текст уходит в лог строкой `handler error`
с `request_id`. `None` → `None`. `map_as(err, Exception, ...)` запрещён: такой маппинг превратил бы
любую ошибку, включая упавшую БД, в выбранный статус.

## Идемпотентность

`POST`/`PUT`/`PATCH` с заголовком `Idempotency-Key` выполняются один раз, повтор получает
сохранённый ответ (`Idempotent-Replayed: true`, воспроизводятся `Content-Type`, `Location`, `ETag`,
`Content-Language`, `Content-Disposition`, `Cache-Control`). Тот же ключ с другим методом, путём,
query или телом → `422 IDEMPOTENCY_KEY_REUSED`; параллельный повтор, пока первый выполняется →
`409 IDEMPOTENCY_IN_PROGRESS`. Ответы `5xx` не сохраняются, запрос или ответ больше `MaxBodyBytes`
идёт без идемпотентности, ошибка store — fail-open (метрика `result=error`) или `503` при
`HTTP_IDEMPOTENCY_FAIL_CLOSED=true`.

Ключ привязан к клиенту: к субъекту, если extractor его положил, иначе к хешу `Authorization` +
`Cookie`.

```python
class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def store(self, cache: cachekit.Cache) -> httpkit.OptionalStore:
        return cachekit.IdempotencyStore(cache)

    @provide
    def auth(self, parser: authnkit.JWTParser) -> httpkit.OptionalAuth:
        async def extract(request: Request) -> object:
            claims = parser.parse_access(bearer(request))
            # Субъект — стабильный scope идемпотентности: ротация токена не превращает
            # ретрай в новую операцию.
            httpkit.bind_subject(str(claims["sub"]))
            return claims

        return extract
```

Контракт store — байтовый, запись сериализует httpkit:

```python
class IdempotencyStore(Protocol):
    async def reserve(self, key: str, ttl: float) -> tuple[str, bool]: ...
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, record: bytes, ttl: float) -> None: ...
    async def release(self, key: str, token: str) -> None: ...
```

Токен обязан быть уникальным на каждый успешный `reserve`: запрос, переживший `reserve_ttl`, своим
опоздавшим `release` не должен снимать чужой резерв. `httpkit.MemoryStore()` — in-memory store для
тестов и одного процесса (истёкшие записи выметаются; `MemoryStore(clock=Tickable(...))` двигает TTL
в тестах).

## Security headers

Всегда включены; пустое поле — заголовок не ставится. CSP по умолчанию пуст:

```bash
HTTP_HSTS="max-age=63072000; includeSubDomains; preload"
HTTP_CSP="default-src 'self'"
```

## CORS / RateLimit за прокси

```bash
HTTP_CORS_ORIGINS="https://app.example.com,https://admin.example.com"
HTTP_CORS_ALLOW_CREDENTIALS=true          # cookie-сессии; несовместимо с "*"
HTTP_CORS_EXPOSE_HEADERS="X-Request-ID,Location,Retry-After,Idempotent-Replayed"

HTTP_RATE_LIMIT_RPS=200
HTTP_RATE_LIMIT_BURST=400
HTTP_RATE_LIMIT_TRUST_PROXY=true          # ровно один доверенный nginx/traefik
```

За reverse-proxy без `trust_proxy` все клиенты приходят с одного IP и делят один bucket; с
`trust_proxy` IP берётся из правого значения `X-Forwarded-For` — того, что дописал прокси. Без
прокси перед сервисом включать нельзя. Тот же адрес для аудита и антибрутфорса:

```python
ip = httpkit.client_ip(request.scope, cfg.rate_limit.trust_proxy)
```

## Auth

Гейт deny-by-default: под ним все маршруты, кроме `/health`, `/ready`, docs и `HTTP_QUIET_PATHS`.
Публичные операции объявляет сам extractor:

```python
PUBLIC = frozenset({"/status", "/webhooks/telegram"})


async def extract(request: Request) -> object:
    if request.url.path in PUBLIC:
        return Anonymous()
    try:
        return parser.parse_access(bearer(request))
    except authnkit.TokenError as exc:
        # TokenExpiredError / TokenSignatureError / WrongTokenTypeError → 401
        raise httpkit.unauthorized("UNAUTHORIZED", "unauthorized") from exc
    except KeyStoreUnavailableError as exc:
        # Недоступная зависимость аутентификации — 503, а не разлогин
        raise httpkit.AuthUnavailableError("app: key store") from exc
```

Ошибка → `401 UNAUTHORIZED`, её текст — только в лог; свой статус задаётся возвратом `APIError`;
инфраструктурная ошибка через `AuthUnavailableError` даёт `503 AUTH_UNAVAILABLE` вместо разлогина.
Identity доступна как `request.state.auth`.

Схемы безопасности OpenAPI:

```python
server = httpkit.Server(
    cfg,
    log,
    auth=extract,
    security_schemes={"Bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}},
)
```

## Свои middleware

```python
from starlette.middleware import Middleware


class TenantMiddleware:
    def __init__(self, app, tenants): ...
    async def __call__(self, scope, receive, send): ...


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def middlewares(self, tenants: TenantRegistry) -> httpkit.Middlewares:
        return (Middleware(TenantMiddleware, tenants=tenants),)
```

Они встают внутрь цепочки кита: их исключения ловит `Recovery`, ответы попадают в метрики и
access-log.

## Readiness-probes

```python
@provide
def probes(self, db: pgkit.DB, cache: cachekit.Cache) -> healthkit.Probes:
    return (pgkit.probe(db), cachekit.probe(cache), healthkit.new("sync", sync.ready))
```

Пробы выполняются параллельно, каждая — не дольше `HTTP_READY_PROBE_TIMEOUT`. Любая упавшая → `503`
с телом `{"status":"unhealthy","probes":[{"name":"pgkit","status":"down"}]}`; текст ошибки — в логе.

## Вебхук с общим секретом

```python
guard = httpkit.shared_secret_guard(
    httpkit.SharedSecretConfig(header="X-Telegram-Bot-Api-Secret-Token", secret=token)
)


@server.app.post("/webhooks/telegram", dependencies=[Depends(guard)])
async def hook(update: dict[str, object]) -> Response: ...
```

Пустой секрет — fail-closed: все вебхуки отбиваются 403.

## Приложение без транспорта

```python
container = make_async_container(httpkit.HttpProvider(autostart=False), AppProvider())
app = await container.get(FastAPI)  # порт не занят, serve не запущен
```

Так делает встроенный webhook-runner бота: он дорегистрирует роут в `.app` и сервит сам.

## Прямой запуск без DI

```python
server = httpkit.Server(httpkit.new_config(), log, probes=(probe,))
server.listen()  # порт занят синхронно, ListenError если занят
task = asyncio.create_task(server.serve())
...
await server.stop()
await task
```
