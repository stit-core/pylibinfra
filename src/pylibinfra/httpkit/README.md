# httpkit

HTTP-сервер на [FastAPI](https://fastapi.tiangolo.com) поверх Starlette/uvicorn с готовой
middleware-цепочкой и единой error-моделью. Один `Server` несёт `app` (`FastAPI`: типобезопасные
операции + OpenAPI) и uvicorn-транспорт. Из коробки: `RequestID`, `OTel`, `AccessLog`, `Metrics`,
`Recovery`, `SecurityHeaders`, `CORS`, `RateLimit`, `MaxBody`, опц. `Idempotency` и `Auth`; ошибки —
`APIError` (`{type,title,status,detail,instance,code}`, `application/problem+json`), незамапленная
ошибка обработчика уходит в лог, а клиенту — нейтральный 500.

`/health` и `/ready` (по пробам `healthkit.Probes`) регистрируются автоматически. RED-метрики и
server-span — by default.

## Импорт

```python
from pylibinfra import httpkit
```

## Быстрый старт

`HttpProvider` provide'ит `Server` и его `FastAPI`, собирает цепочку из `Config`, регистрирует
`/health` + `/ready` и привязывает lifecycle: порт занимается синхронно (занятый адрес — `ListenError`
и провал старта), graceful shutdown — на закрытии контейнера. `Config` регистрирует caller.

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import healthkit, httpkit
from pylibinfra.logkit import Logger, nop


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def http_config(self) -> httpkit.Config:
        return httpkit.new_config()  # HTTP_* из ENV + валидация

    @provide
    def logger(self) -> Logger:
        return nop()  # в проде — logkit.LogkitProvider

    @provide
    def probes(self) -> healthkit.Probes:
        return (healthkit.new("app", ready),)  # пробы китов складывает App-Provider


container = make_async_container(httpkit.HttpProvider(), AppProvider())
server = await container.get(httpkit.Server)


@server.app.get("/users/{uid}")
async def get_user(uid: str) -> dict[str, str]:
    try:
        return await service.get(uid)
    except LookupError as exc:
        raise httpkit.not_found("USER_NOT_FOUND", "user not found") from exc
```

Без DI (caller сам решает, когда сервить):

```python
server = httpkit.Server(httpkit.new_config(), log, probes=(probe,))
server.listen()  # порт занят синхронно
await server.serve()  # обслуживает до stop()
```

## Документация

- [docs/architecture.md](docs/architecture.md) — раскладка цепочки на `app.add_middleware`, единая
  обёртка ответа, точки расширения, error-модель, auth, идемпотентность, health, SSE/WebSocket.
- [docs/configuration.md](docs/configuration.md) — справочник ENV: `Config`, `CORSConfig`,
  `RateLimitConfig`, `MaxBodyConfig`, `SecurityHeadersConfig`, `IdempotencyConfig`, правила
  валидации.
- [docs/observability.md](docs/observability.md) — метрики, labels, спаны, логи.
- [docs/usage.md](docs/usage.md) — рецепты: роуты, `map_is`/`map_as`, идемпотентность,
  CORS/RateLimit за прокси, auth, свои middleware и security-схемы, readiness.
- [docs/testing.md](docs/testing.md) — `testkit/handlertest`, тесты middleware, метрик и проводки.

## Инварианты и подводные камни

- **Один response-wrapper на запрос.** Статус, объём тела и заголовки кита живут в общей обёртке
  `send`, поэтому security- и CORS-заголовки получает любой ответ — включая 429 лимитера, 413
  `MaxBody` и нейтральный 500 `Recovery`.
- **Auth — deny-by-default.** В ASGI нет huma-шного `Operation().Security`: гейт закрывает все
  маршруты, кроме `/health`, `/ready`, docs и `HTTP_QUIET_PATHS`. Публичные операции объявляет сам
  extractor, возвращая анонимную identity.
- **`HTTP_READ_TIMEOUT` не имеет прямого аналога в uvicorn** — валидируется и держится как часть
  ENV-поверхности; за медленным клиентом следит reverse-proxy. Idle-таймаут доезжает до
  `timeout_keep_alive`, shutdown — до `timeout_graceful_shutdown`.
- **`MaxBody` ловит превышение только при чтении тела** (как `MaxBytesReader` в Go): объявленный
  `Content-Length` отбивается сразу, тело без него — по мере чтения обработчиком.
- **Метрики создаются на текущем `Meter`**: тест, поставивший свой `MeterProvider`, должен сделать
  это до `build_app`/`Server`.

Версия, установка и стабильность API — в [корневом README](../../../README.md).
