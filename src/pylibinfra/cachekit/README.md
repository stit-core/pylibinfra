# cachekit

Обёртка над Redis для сервисов stit. Протокол `Cache` — байтовый key-value с
TTL, атомарными `set_nx` (резерв/lock), `get_del` (одноразовые токены) и
`compare_and_delete` (снятие собственного резерва), тегированной инвалидацией,
`ping`/`aclose`. Поверх него типизированные `get_json`/`set_json`,
`IdempotencyStore` для идемпотентности httpkit, `MemoryCache` и health-проба для
`/ready`.

Кит — про доступ к Redis и сериализацию; политику ключей и TTL определяет
потребитель.

Требуется **Redis >= 7.0**: TTL набора тега поддерживается через `PEXPIRE … GT`.
`CachekitProvider` проверяет версию сервера (`INFO server`) там же, где делает
`PING`, и валит старт `cachekit.ServerVersionError`: на 6.x первая запись в новый
тег прошла бы, а вторая упала ошибкой скрипта. Redis-совместимый сервер, который
не отдаёт `redis_version`, — `REDIS_SKIP_VERSION_CHECK=true`. Cluster-режим кит
не поддерживает: инвалидация тега удаляет ключи, не объявленные в `KEYS`.

Версия и установка — корневой [`README.md`](../../../README.md).

## Импорт

```python
from pylibinfra import cachekit
```

## Быстрый старт

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import cachekit, healthkit


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def cache_config(self) -> cachekit.Config:
        return cachekit.new_config()  # REDIS_* из ENV; caller провайдит явно

    @provide
    def probes(self, cache: cachekit.Cache) -> healthkit.Probes:
        return (cachekit.probe(cache),)  # /ready через httpkit


container = make_async_container(cachekit.CachekitProvider(), AppProvider())

cache = await container.get(cachekit.Cache)
await cachekit.set_json(cache, "user:42", {"name": "alice"}, 300.0)
profile: dict[str, str] | None = await cachekit.get_json(cache, "user:42")

await cache.set_with_tags("user:42:orders", b"[]", 3600.0, "user:42")
await cache.invalidate_by_tag("user:42")

await container.close()  # PING на старте, aclose() на стопе
```

Без dishka — `cachekit.new_cache(cfg)`; закрывать `aclose()` самому:

```python
cache = cachekit.new_cache(cachekit.new_config())
try:
    await cache.set("session:abc", b"{}", 900.0)
finally:
    await cache.aclose()
```

## Конфигурация

`cachekit.Config` — public API (корневой `README.md` § Стабильность API).
`new_config()` читает `REDIS_*` из окружения и валидирует их: невалидное значение
валит старт `cachekit.ConfigError`, а не первую команду. Текст ошибки называет
ENV-переменную и не содержит пароля из `REDIS_URL`.

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `REDIS_URL` | `url` | `SecretStr` | `redis://localhost:6379/0` | Адрес Redis (`redis://`, `rediss://`, `unix://`); обязан разбираться `redis.asyncio` |
| `REDIS_KEY_PREFIX` | `key_prefix` | `str` | пусто | Префикс **всех** ключей кита, включая `tag:` и `idempotency:` |
| `REDIS_POOL_SIZE` | `pool_size` | `int` | `10` | Размер пула соединений; `>= 1` |
| `REDIS_MAX_RETRIES` | `max_retries` | `int` | `3` | Повторы команды после сетевой ошибки; `-1` отключает, `>= -1` |
| `REDIS_DIAL_TIMEOUT` | `dial_timeout` | `float`, секунды | `5.0` | Таймаут установления соединения; `>= 0` (`0` — без ограничения) |
| `REDIS_READ_TIMEOUT` | `read_timeout` | `float`, секунды | `3.0` | Верхняя граница ожидания ответа; `>= 0` (`0` — без ограничения) |
| `REDIS_WRITE_TIMEOUT` | `write_timeout` | `float`, секунды | `3.0` | Верхняя граница записи команды; `>= 0` |
| `REDIS_PING_ON_START` | `ping_on_start` | `bool` | `true` | `CachekitProvider` делает `PING` и проверку версии при выдаче клиента: недоступный Redis валит старт, а не первый запрос |
| `REDIS_SKIP_VERSION_CHECK` | `skip_version_check` | `bool` | `false` | Снимает проверку `redis_version >= 7`; для redis-совместимых серверов, не отдающих это поле в `INFO` |

**Query-опции `REDIS_URL`.** `redis.asyncio` понимает часть настроек прямо в
адресе (`db`, `protocol`, `client_name`, `health_check_interval`,
`decode_responses`, `ssl_cert_reqs`, …) и в конфликте **query-опция сильнее**
одноимённой `REDIS_*` — так устроен `Redis.from_url`. Не задавайте в URL
`socket_timeout`, `socket_connect_timeout` и `max_connections`, если хотите
управлять ими через `REDIS_*`.

**Один сокет-дедлайн.** `redis.asyncio` ставит на команду один таймаут сокета:
кит берёт его из `REDIS_READ_TIMEOUT` (ожидание ответа доминирует).
`REDIS_WRITE_TIMEOUT` валидируется и остаётся в конфиге ради паритета ENV со
stit-сервисами на Go; отдельного дедлайна на запись у драйвера нет.

**Namespace.** Служебные ключи (`tag:`, `idempotency:`) фиксированы, поэтому два
сервиса на одной БД Redis делят их пространство. Изолируйте сервисы: свой
`REDIS_KEY_PREFIX`, своя БД (`redis://host:6379/N`) или свой Redis.

## Observability

Instrumentation-scope `pylibinfra.cachekit`, глобальные Meter/Tracer (no-op без
настроенного OTel-SDK). Клиент кита наблюдает каждую команду:

| Сигнал | Тип | Labels | Назначение |
|---|---|---|---|
| `cache_commands_total` | Counter | `cmd`, `result` | Команды по имени; `result` — `ok`, `miss` (nil-ответ), `error` |
| `cache_command_duration_seconds` | Histogram (`s`) | `cmd` | Round-trip команды; общие бакеты длительности |
| спан `redis <cmd>` | Client span | `db.system=redis`, `db.operation` | Латентность Redis внутри трейса запроса; статус `Error` на отказе |

`cmd` — первое слово команды в нижнем регистре: `get`, `set` (и `set`, и
`set_nx`), `getdel`, `unlink` (`delete`), `evalsha` (`set_with_tags`,
`compare_and_delete`, `invalidate_by_tag` — `SPOP`+`UNLINK` внутри скрипта
отдельными командами не видны), `script` (загрузка скрипта), `ping`, `info`
(проверка версии на старте). Первый `evalsha` на новом сервере отвечает
`NOSCRIPT` (`result=error`) — драйвер тут же грузит скрипт (`cmd=script`) и
повторяет; на алертах это единичный datapoint на инстанс Redis, а не ошибка кита.

Ключи и значения в атрибуты спанов не попадают: PII и неограниченная
кардинальность.

Health-проба:

```python
cachekit.probe(cache)  # healthkit.Probe с именем "cachekit": PING
```

`CachekitProvider` не кладёт пробу в контейнер сам — её собирает консьюмер в
`healthkit.Probes` (см. § Быстрый старт).

## API

### `Cache`

```python
class Cache(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, ttl: float) -> None: ...
    async def set_nx(self, key: str, value: bytes, ttl: float) -> bool: ...
    async def get_del(self, key: str) -> bytes | None: ...
    async def compare_and_delete(self, key: str, expected: bytes) -> bool: ...
    async def delete(self, *keys: str) -> None: ...
    async def set_with_tags(self, key: str, value: bytes, ttl: float, *tags: str) -> None: ...
    async def invalidate_by_tag(self, tag: str) -> None: ...
    async def ping(self) -> None: ...
    async def aclose(self) -> None: ...
```

- `get`/`get_del` — промах это `None`, а не исключение.
- `set`/`set_with_tags` — `ttl == 0` означает ключ без срока жизни; TTL — секунды
  (`float`), субмиллисекундный округляется вверх до 1 ms.
- `set_nx` — `True`, если ключ записан (его не было); `False`, если занят.
- `compare_and_delete` — удаляет ключ, только если его значение совпадает с
  `expected`; `False` без ошибки, если значение другое или ключа нет.
- `set_with_tags` — `SET` + `SADD tag:<tag> key` одним атомарным скриптом; набор
  тега живёт не меньше самого долгоживущего своего члена.
- `invalidate_by_tag` — порциями по 256 членов, `SPOP` + `UNLINK` одним скриптом;
  отсутствующий тег не ошибка.
- `aclose` идемпотентен.

Реализации: `RedisCache` (production) и `MemoryCache` (тесты, single-replica).

### `get_json` / `set_json`

```python
async def get_json[T](cache: Cache, key: str) -> T | None
async def set_json[T](cache: Cache, key: str, value: T, ttl: float) -> None
```

`json` поверх `get`/`set`. Промах — `None`; битое значение — `CacheDecodeError`:
испорченная запись отличается от промаха, чтобы потребитель мог её залогировать,
а не молча перезаписать. Тип `T` задаётся аннотацией call-site'а
(`profile: Profile | None = await get_json(cache, key)`).

Записанный через `set_json` JSON-`null` читается тем же `None`, что и промах, —
кладите вместо него значение-обёртку (`{"value": None}`) либо `set`/`get` с
собственной сериализацией.

### `IdempotencyStore`

```python
class IdempotencyStore:
    def __init__(self, cache: Cache) -> None: ...
    async def reserve(self, key: str, ttl: float) -> tuple[str, bool]: ...
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, record: bytes, ttl: float) -> None: ...
    async def release(self, key: str, token: str) -> None: ...
```

Байтовый store идемпотентности httpkit (структурно совместим с его протоколом
`IdempotencyStore`): `reserve` — `SET NX` маркера с токеном владельца на время
выполнения запроса, `set` — готовая запись, `get` не отдаёт маркер как запись.
`release` снимает резерв **только по своему токену** (`compare_and_delete`): ни
готовая запись, ни резерв следующего запроса (предыдущий истёк по своему TTL)
опоздавшим `release` не сносятся. Ключи живут в namespace `idempotency:`.

`CachekitProvider` кладёт store в контейнер под своим типом; в ключ httpkit его
переводит консьюмер:

```python
@provide
def idempotency(self, store: cachekit.IdempotencyStore) -> httpkit.IdempotencyStore:
    return store
```

### `MemoryCache`

```python
cachekit.MemoryCache(clock: clockkit.Clock | None = None)
```

In-memory реализация `Cache` с той же семантикой TTL, `set_nx`, `get_del`,
`compare_and_delete` и тегов. Годится для unit-тестов потребителя и для
single-replica развёртывания без Redis: истёкшие ключи и членство в тегах
вычищаются амортизированным проходом на записи, поэтому ключ, который никто не
читает, не живёт вечно. Данные лежат в процессе — при рестарте и при второй
реплике их нет.

`clock` подменяет часы: тест двигает TTL через `clockkit.Tickable` вместо сна.

```python
clock = clockkit.Tickable(datetime(2026, 1, 1, tzinfo=UTC))
cache = cachekit.MemoryCache(clock)
await cache.set("k", b"v", 60.0)
clock.advance(120.0)
assert await cache.get("k") is None
```

## Тестирование

Интеграционные тесты поднимают Redis через `testkit.start_redis` (образ
`REDIS_IMAGE`, переопределяется `image=`) и пропускаются без Docker;
`MemoryCache`, `IdempotencyStore`, наблюдаемость команды, `Config`,
`CachekitProvider` и работа с недоступным сервером покрыты unit-тестами. Один
контрактный набор гоняется на обеих реализациях `Cache`.

```python
async with start_redis() as server, server.cache() as cache:
    await cache.set("k", b"v", 60.0)
```

В тестах потребителя фейком кэша служит `cachekit.MemoryCache()` — та же
семантика TTL, тегов и `compare_and_delete`, без Docker.

```bash
uv run pytest -m "not integration" tests/cachekit   # unit
uv run pytest tests/cachekit                        # + Redis в testcontainers
```

## Инварианты и подводные камни

- **Эффективный таймаут команды — `min(дедлайн вызывающего, REDIS_READ_TIMEOUT)`.**
  Дедлайн вызывающего в asyncio — это отмена (`asyncio.timeout(...)` снаружи
  вызова): она доходит до сокета, поэтому HTTP-хендлер с бюджетом 500 ms не ждёт
  зависший Redis три секунды. Без внешнего дедлайна команда ограничена только
  таймаутами конфига.
- **После сетевого таймаута команда может быть повторена.** При
  `REDIS_MAX_RETRIES > 0` драйвер шлёт команду заново, поэтому `set_nx` может
  вернуть `False`, а `get_del` — промах, хотя первая попытка уже применилась на
  сервере (atomic на сервере, at-least-once на клиенте). Где это неприемлемо
  (резерв идемпотентности, одноразовый refresh-токен), ставьте
  `REDIS_MAX_RETRIES=-1` и обрабатывайте ошибку ретраем на своём уровне.
- **`get_del` и `set_nx` атомарны на сервере** — используйте их для одноразовых
  state/refresh-токенов и резервов вместо `get`+`delete` / `get`+`set`.
- **Набор тега живёт не меньше самого долгоживущего члена.** Запись с бо́льшим
  TTL продлевает набор, с меньшим — не укорачивает, запись без TTL делает набор
  персистентным. Члены могут истечь раньше набора — `invalidate_by_tag` просто
  удалит уже отсутствующие ключи.
- **Инвалидация не теряет конкурентные записи.** `SPOP` снимает членство до
  удаления ключей: ключ, добавленный в тег во время инвалидации, либо попадёт в
  текущую порцию, либо останется членом набора и уйдёт при следующем вызове.
  Обе команды — один скрипт: сетевой сбой между ними иначе оставил бы ключи в
  Redis вне какого-либо тега, и повторная инвалидация их бы не нашла.
- **Членство в теге привязано к имени ключа и переживает значение.** Ни `delete`,
  ни `get_del`, ни истечение TTL членства не снимают — ключ, записанный под тем же
  именем заново уже без тегов, всё равно уйдёт при `invalidate_by_tag` прежнего
  тега. Обе реализации ведут себя так; в `MemoryCache` мёртвое членство
  вычищается тем же амортизированным проходом, что и истёкшие ключи.
- **Служебные префиксы:** `tag:` (наборы тегов) и `idempotency:`; не используйте
  их для своих ключей. `REDIS_KEY_PREFIX` добавляется поверх них.
- **Отказы драйвера кит не оборачивает.** `redis.exceptions.ConnectionError`,
  `TimeoutError` и `ResponseError` поднимаются как есть — консьюмер различает их
  `except`'ом. Свои ошибки кита (`ConfigError`, `ServerVersionError`,
  `CacheDecodeError`) наследуют `CacheError` и начинаются с `cachekit: `.
- **`aclose()` не хоронит клиент.** Он освобождает соединения пула и идемпотентен,
  но следующая команда откроет соединение заново — «закрытый» кэш в Python не
  отвечает ошибкой, в отличие от закрытого пула asyncpg.
- **Читайте конфиг через `new_config()`.** Прямой `Config(...)` поднимает
  pydantic `ValidationError`, в repr которой попадает входное значение — включая
  пароль из `REDIS_URL`.
- **`PING` и проверка версии — при выдаче клиента, `aclose()` — на закрытии
  контейнера.** Без dishka закрывайте кэш сами; `REDIS_PING_ON_START=false` —
  только для окружений, где Redis поднимается позже сервиса: вместе с `PING` оно
  снимает и проверку версии.
