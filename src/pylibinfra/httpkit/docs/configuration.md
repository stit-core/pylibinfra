# httpkit — конфигурация

`Config` агрегирует настройки сервера и вложенные конфиги middleware. `new_config()` читает
окружение и валидирует конфиг целиком; невалидное значение — `httpkit.ConfigError`, оно валит старт
приложения, а не первый запрос. Прямой `Config(...)` даёт pydantic `ValidationError` (тоже
`ValueError`) — это допустимо для тестов и ручной сборки.

Длительности — **секунды (float)**, размеры — **байты (int)**, списки — запятая-разделённые строки
в ENV.

## Config

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `HTTP_ADDR` | `addr` | `str` | `:8080` | bind-адрес; пустой host — все интерфейсы (dual-stack IPv6+IPv4, `Server.addr()` → `[::]:8080`); `:0` — свободный порт; не пуст |
| `HTTP_READ_TIMEOUT` | `read_timeout` | `float`, секунды | `15.0` | потолок чтения запроса; `>= 0`. Прямого аналога в uvicorn нет — за медленным клиентом следит reverse-proxy |
| `HTTP_IDLE_TIMEOUT` | `idle_timeout` | `float`, секунды | `60.0` | keep-alive соединения (`timeout_keep_alive`); `>= 0` |
| `HTTP_SHUTDOWN_TIMEOUT` | `shutdown_timeout` | `float`, секунды | `15.0` | дедлайн graceful-shutdown; `> 0` |
| `HTTP_API_TITLE` | `api_title` | `str` | `API` | title в OpenAPI; не пуст |
| `HTTP_API_VERSION` | `api_version` | `str` | `0.1.0` | version в OpenAPI; не пуст |
| `HTTP_DOCS_ENABLED` | `docs_enabled` | `bool` | `true` | `false` убирает `/docs`, `/redoc`, `/openapi.json` |
| `HTTP_READY_PROBE_TIMEOUT` | `ready_probe_timeout` | `float`, секунды | `5.0` | потолок одной пробы `/ready`; `> 0` |
| `HTTP_QUIET_PATHS` | `quiet_paths` | `list[str]` | `/health,/ready` | пути без Info-строки access-log и без server-спана |
| — | `cors` | `CORSConfig` | — | вложенный |
| — | `rate_limit` | `RateLimitConfig` | — | вложенный |
| — | `max_body` | `MaxBodyConfig` | — | вложенный |
| — | `security` | `SecurityHeadersConfig` | — | вложенный |
| — | `idempotency` | `IdempotencyConfig` | — | вложенный |

Таймауты чтения заголовков и записи ответа поверхности не имеют: uvicorn их не
предоставляет (`h11`/`httptools` держат только `timeout_keep_alive` и
`timeout_graceful_shutdown`), поэтому slowloris и медленного читателя отсекает reverse-proxy
перед сервисом. `HTTP_READ_TIMEOUT` остаётся частью ENV-поверхности и валидируется, но в
транспорт не доезжает.

## CORSConfig

No-op при пустом `origins`. Конкретный origin эхается с `Vary: Origin`; origin, разрешённый только
по `*`, всегда получает `*` **без** credentials — отражать произвольный origin вместе с credentials
небезопасно.

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `HTTP_CORS_ORIGINS` | `origins` | `list[str]` | пусто (CORS off) | разрешённые Origin; `*` = любой |
| `HTTP_CORS_METHODS` | `methods` | `list[str]` | `GET,POST,PUT,PATCH,DELETE,OPTIONS` | `Access-Control-Allow-Methods` |
| `HTTP_CORS_HEADERS` | `headers` | `list[str]` | `Authorization,Content-Type,X-Request-ID` | `Access-Control-Allow-Headers` |
| `HTTP_CORS_EXPOSE_HEADERS` | `expose_headers` | `list[str]` | `X-Request-ID,Location,Retry-After,Idempotent-Replayed` | `Access-Control-Expose-Headers`: без него cross-origin JS не читает даже заголовки, которые ставит сам кит |
| `HTTP_CORS_MAX_AGE` | `max_age` | `int`, секунды | `600` | `Access-Control-Max-Age`; `>= 0` |
| `HTTP_CORS_ALLOW_CREDENTIALS` | `allow_credentials` | `bool` | `false` | `Access-Control-Allow-Credentials: true`; несовместим с origin `*` (ошибка валидации) |

## RateLimitConfig

Token-bucket на IP клиента. Превышение → `429` с `Retry-After: 1` и `application/problem+json`.
Простаивающие bucket'ы выметаются (5 минут без запросов), память ограничена активными клиентами.

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `HTTP_RATE_LIMIT_RPS` | `rps` | `int` | `100` | запросов в секунду на IP; `0` — off; `>= 0` |
| `HTTP_RATE_LIMIT_BURST` | `burst` | `int` | `200` | ёмкость bucket; `0` → равна `rps`; `>= 0` |
| `HTTP_RATE_LIMIT_TRUST_PROXY` | `trust_proxy` | `bool` | `false` | IP берётся из **правого** значения последнего `X-Forwarded-For`, затем `X-Real-IP` |

`trust_proxy` рассчитан на топологию «ровно один доверенный прокси»: nginx/traefik дописывают
реальный IP в конец `X-Forwarded-For` (`$proxy_add_x_forwarded_for`), а всё, что левее, прислал
клиент. Клиент вправе прислать и **свой** заголовок `X-Forwarded-For`, поэтому берётся правое
значение **последнего** из них (так же с `X-Real-IP`) — прокси, который добавляет заголовок вместо
замены, тоже безопасен. Значение должно парситься как IP, иначе берётся `X-Real-IP`, иначе адрес
соединения. Без прокси перед сервисом включать нельзя — иначе клиент подделает ключ лимитера. Ту же
логику отдаёт `httpkit.client_ip(scope, trust_proxy)`: используйте её в антибрутфорсе и аудите,
чтобы ключ клиента был один на весь сервис.

## MaxBodyConfig

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `HTTP_MAX_BODY_BYTES` | `max_bytes` | `int`, байты | `1048576` (1 MiB) | лимит тела; `0` — off; `>= 0` |

Объявленный `Content-Length` больше лимита отбивается до чтения; тело без него считается по мере
чтения и поднимает `httpkit.MaxBodyError` из `receive`. Оба исхода — `413 PAYLOAD_TOO_LARGE` и
`http_max_body_rejections_total`.

## SecurityHeadersConfig

Заголовки на каждый ответ; пустое значение — заголовок не ставится.

| Env | Поле | Тип | Default | Назначение |
|---|---|---|---|---|
| `HTTP_HSTS` | `hsts` | `str` | `max-age=31536000; includeSubDomains` | `Strict-Transport-Security` |
| `HTTP_CONTENT_TYPE_OPTIONS` | `content_type_options` | `str` | `nosniff` | `X-Content-Type-Options` |
| `HTTP_FRAME_OPTIONS` | `frame_options` | `str` | `DENY` | `X-Frame-Options` |
| `HTTP_REFERRER_POLICY` | `referrer_policy` | `str` | `strict-origin-when-cross-origin` | `Referrer-Policy` |
| `HTTP_CSP` | `csp` | `str` | пусто | `Content-Security-Policy` |

## IdempotencyConfig

Применяется, когда в контейнере есть `IdempotencyStore`.

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `HTTP_IDEMPOTENCY_HEADER` | `header` | `str` | `Idempotency-Key` | заголовок с ключом; не пуст |
| `HTTP_IDEMPOTENCY_TTL` | `ttl` | `float`, секунды | `86400.0` (24 ч) | срок хранения ответа; `> 0` |
| `HTTP_IDEMPOTENCY_RESERVE_TTL` | `reserve_ttl` | `float`, секунды | `60.0` | сколько ключ считается «выполняется», если процесс упал в середине запроса; `> 0` |
| `HTTP_IDEMPOTENCY_MAX_BODY_BYTES` | `max_body_bytes` | `int`, байты | `65536` | потолок буферизации запроса и ответа; больше — запрос идёт без идемпотентности; `> 0` |
| `HTTP_IDEMPOTENCY_STORE_TIMEOUT` | `store_timeout` | `float`, секунды | `2.0` | потолок одной операции store; запись результата не зависит от отмены клиента; `> 0` |
| `HTTP_IDEMPOTENCY_FAIL_CLOSED` | `fail_closed` | `bool` | `false` | `true` — недоступный store отвечает `503 IDEMPOTENCY_UNAVAILABLE`; `false` — запрос выполняется без дедупликации (fail-open) |

## Правила валидации целиком

- `addr` не пуст; `read_timeout`, `idle_timeout` `>= 0`; `shutdown_timeout > 0`;
  `ready_probe_timeout > 0`.
- `api_title` и `api_version` не пусты.
- `rate_limit.rps`, `rate_limit.burst`, `max_body.max_bytes`, `cors.max_age` `>= 0`.
- `idempotency`: `header` не пуст, `ttl`, `reserve_ttl`, `store_timeout`, `max_body_bytes` `> 0`.
- `cors.allow_credentials` несовместим с origin `*`.

Zero-value `Config()` безопасен и полезен: все дефолты валидны, сервер поднимается на `:8080` с
включёнными security-заголовками, лимитером и лимитом тела.

## Public API

Имена полей и `validation_alias` (ENV-имена) всех перечисленных структур — part of public API
(корневой [README](../../../../README.md) § Стабильность API).
