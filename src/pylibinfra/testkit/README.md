# testkit

Тестовый инструментарий pylibinfra: testcontainers-обёртки для интеграционных
тестов (Postgres / NATS / Redis / MinIO) и per-kit test-helper'ы (fake-реализации
портов китов, recorder'ы, харнессы для FastAPI-хендлеров, gRPC и aiogram-бота).
Импортируется только из тестов консьюмера — в production-граф не попадает.
Контейнерные helper'ы требуют extra `[testkit]` (testcontainers, pytest,
pytest-asyncio); сабпакеты — нет.

## Назначение

Сервис, собранный на китах pylibinfra, тестируется на их портах. testkit закрывает
две задачи:

- **Интеграционный слой.** `start_postgres` / `start_nats` / `start_redis` /
  `start_minio` поднимают реальный контейнер на свободном порту и гасят его на
  выходе из `async with`. Тест получает DSN/URL живого инстанса — без моков самой
  БД/брокера/хранилища.
- **Юнит-слой.** Сабпакеты дают детерминированные fake-реализации портов китов
  (`auditkit.Logger`, `authnkit.Hasher`, `bruteforcekit.Guard`,
  `outboxkit.Publisher`, `pgkit.Transactor`, `natskit.Bus`/`StreamBus`,
  `logkit.Logger`), контрактный набор для собственных реализаций `storagekit`
  (`storagetest`) и харнессы для тестов FastAPI-хендлеров (`handlertest`),
  gRPC-сервисов (`grpctest`) и aiogram-диалогов (`bottest`).

## Импорт

```python
from pylibinfra import testkit  # контейнерные helper'ы
from pylibinfra.testkit import outboxtest  # сабпакет (на примере outboxtest)
```

Все тестовые сабпакеты живут под `testkit/` — второго корня импорта нет.

## Быстрый старт

Контейнерные helper'ы — async context-manager'ы (Python-идиома вместо Go-шного
`t.Cleanup`), поэтому API не завязан на pytest. Без Docker они пропускают тест
(`unittest.SkipTest`, его понимает и pytest) — собственный гейт в тесте не нужен.

```python
import pytest

from pylibinfra import pgkit, testkit
from pylibinfra.auditkit import pgstore as auditpg

pytestmark = pytest.mark.integration


async def test_repository() -> None:
    async with testkit.start_postgres(
        migrations=[auditpg.MIGRATIONS],  # миграции китов — их Source
        migrations_dir="tests/migrations",  # миграции приложения (источник `app`)
    ) as pg:
        db = await pg.db()  # pgkit.DB поверх общего пула фикстуры
        tx = await pg.transactor()  # pgkit.Transactor поверх того же пула
        dsn = pg.dsn  # на случай прямого подключения
        ...

    async with testkit.start_nats(jetstream=True) as ns:  # без флага — core NATS
        async with ns.bus() as bus:
            ...

    async with testkit.start_redis() as rd:
        async with rd.cache() as cache:
            ...

    async with testkit.start_minio() as mn:
        cfg = mn.config("media")  # storagekit.Config на живой MinIO
```

Сигнатуры:

```python
def require_docker() -> None                       # SkipTest, если Docker недоступен
def docker_available() -> bool

async with start_postgres(*, migrations: Sequence[pgkit.Source] = (), migrations_dir: str | None = None,
                          image: str = POSTGRES_IMAGE) as pg: ...   # PostgresFixture
    pg.dsn; pg.container; pg.pool_config() -> pgkit.PoolConfig
    await pg.pool() -> Pool; await pg.db() -> pgkit.DB; await pg.transactor() -> pgkit.Transactor

async with start_nats(*, jetstream: bool = False, image: str = NATS_IMAGE) as ns: ...   # NatsFixture
    ns.url; ns.container; ns.config() -> natskit.Config; ns.conn() / ns.bus() / ns.stream_bus()

async with start_redis(*, image: str = REDIS_IMAGE) as rd: ...                          # RedisFixture
    rd.url; rd.container; rd.config() -> cachekit.Config; rd.cache(key_prefix="")

async with start_minio(*, image: str = MINIO_IMAGE) as mn: ...                          # MinioFixture
    mn.endpoint; mn.access_key; mn.secret_key; mn.container; mn.config(bucket, **overrides) -> storagekit.Config
```

`start_postgres` применяет переданные источники через `pgkit.migrate_up_all` —
тем же путём, что `pgkit.PgMigrateProvider` в приложении (таблица версий на
источник, порядок применения — киты по имени, затем `app`); без опций миграции
не прогоняются. `jetstream=True` запускает `nats-server -js` — нужен тестам
`natskit.StreamBus` и `outboxkit`.

Юнит-helper из сабпакета — fake без контейнера:

```python
from pylibinfra.testkit import outboxtest


async def test_publishes_event() -> None:
    bus = outboxtest.Recorder()  # реализует outboxkit.Publisher
    svc = Service(bus)

    await svc.create_user("u1")

    assert bus.count() == 1
    assert bus.last("user.created") is not None
```

## Конфигурация

Конфиг-классов у testkit нет: поведение задаётся аргументами helper'ов,
ENV-переопределений образов нет тоже. Образ каждого контейнера —
константа, а параметр `image=` подменяет её точечно (расширения вроде pgvector,
другая мажорная версия):

| Константа | Значение | Порты |
| --- | --- | --- |
| `POSTGRES_IMAGE` | `postgres:17-alpine` | `5432/tcp`, база/пользователь/пароль `test` |
| `NATS_IMAGE` | `nats:2.14.6-alpine` | `4222/tcp` |
| `REDIS_IMAGE` | `redis:7-alpine` | `6379/tcp` |
| `MINIO_IMAGE` | `minio/minio:RELEASE.2025-01-20T14-49-07Z` | креды в `MinioFixture` |

Требования к окружению для контейнерных helper'ов: доступный Docker-демон, к
которому подключается testcontainers-python (адрес — `DOCKER_HOST` /
`TESTCONTAINERS_HOST_OVERRIDE`), и возможность тянуть указанные образы.
Сабпакеты Docker не требуют.

Часть фейков живёт в самих китах, а не в testkit, — там, где реализация полезна и
в проде: `cachekit.MemoryCache()` закрывает `cachekit.Cache` без Redis,
`storagekit.MemoryStorage()` — `storagekit.Storage`/`Presigner` без S3
(`storagetest.run_contract` прогоняет по ним тот же контракт, что по боевому
адаптеру), `httpkit.MemoryStore` — store идемпотентности, `clockkit.Fixed`/`Tickable`
— часы теста.

## Сабпакеты

Соглашение об именах: `<kit без суффикса kit>test` (`auditkit` → `audittest`,
`outboxkit` → `outboxtest`). Исключения — `handlertest` (обслуживает
FastAPI-хендлеры поверх `httpkit`, а не сам кит) и `bottest` (aiogram-диалоги).

| Сабпакет | Обслуживает | Что даёт |
| --- | --- | --- |
| `audittest` | `auditkit` | `Recorder` — in-memory `auditkit.Logger`: `log` валидирует `Entry` как pgstore (`InvalidEntryError`) и хранит копию `metadata`; запросы `entries`/`actions`/`has`/`last`/`by_resource` + `reset`. |
| `authntest` | `authnkit` | `DeterministicHasher` — async `authnkit.Hasher` без Argon2 (хеш = `"hash:"+plain`), падает вне pytest; `HASH_PREFIX`, `strip_hash_prefix`. |
| `bottest` | `tgbotkit` | `JsonMemoryStorage` (ловит несериализуемый dialog_data), `MemoryRedis`/`MemoryLock` (подмножество `redis.Redis` с TTL по `clockkit.Clock` и Lua-скриптами кита), `BotClient`/`MockMessageManager`, `bind_dialogs`. Требует extra `[tgbotkit]`. |
| `bruteforcetest` | `bruteforcekit` | `bruteforcekit.Guard`-фейки, все проверяют контракт ключа как pgstore (`InvalidKeyError`): `Permissive`, `Locked(until)`, `Recorder` (`attempts()`/`success_attempts()`), `Counting(cfg, clock)` (политика pgstore в памяти; единственный, кто агрегирует IPv6 и реализует `Cleaner`). |
| `grpctest` | `grpckit` | `running_server(*services, ...)` — боевой `grpckit.Server` на UDS или TCP `:0`, отдаёт target канала. |
| `handlertest` | `httpkit` | `new_app`, `new_client` (httpx ASGI-транспорт), `APIError`/`APIErrorLocation`, `decode_api_error`, `assert_status`, `assert_api_error`, `assert_json`. |
| `logtest` | `logkit` | `Capture`/`capture_at(level)` — `logkit.Logger`, копящий записи в памяти: `entries`, `find`, `find_func`, `has`, `reset`; `Entry.str`/`Entry.value` читают поля записи. |
| `natstest` | `natskit` | `Recorder` — in-memory `natskit.Bus`/`StreamBus` с синхронной доставкой: `publish`/`publish_stream`/`publish_stream_batch`, `subscribe*`, запросы `published`/`published_on`/`subscriptions`/`handler_errors`, инжекторы `deliver` и `set_publish_error` + `reset`. |
| `outboxtest` | `outboxkit` | `Recorder` — in-memory `outboxkit.Publisher` (валидирует payload как боевой `Bus`); запросы `all`/`published`/`last`/`count` + `reset`. |
| `pgtest` | `pgkit` | `pgkit.Transactor`-фейки: `Identity` (прогоняет `fn` без транзакции), `Recorder` (то же + счётчик `calls`); оба помечают контекст через `pgkit.with_test_tx`, поэтому код с гейтом на `pgkit.in_tx` проходит без Postgres. `in_rollback_tx(pool)` — реальная транзакция с откатом после теста. |
| `storagetest` | `storagekit` | `run_contract(subject_factory)` / `run_case` — общий контрактный набор (`CASES`) для `storagekit.Storage`/`Presigner`: боевой адаптер и in-memory проверяются одними кейсами; `Subject(storage, presigner, keys=)` описывает проверяемую реализацию. |

## Observability

testkit ничего не эмитит: ни метрик, ни спанов, ни логов через `logkit`.
Контейнерные helper'ы и fake'и сообщают о проблемах только исключениями.
Диагностика testcontainers приглушена — это тестовый код, не часть рантайма.

## Тестирование

Helper'ы вызываются прямо из тестов консьюмера.

- `pytest -m "not integration"` — контейнеры не поднимаются (аналог `go test -short`).
- `pytest` — полный прогон, включая контейнерные тесты (нужен Docker); без демона
  контейнерные тесты пропускаются, а не падают.

Сабпакетные fake'и Docker не требуют. Собственные тесты testkit устроены так же:
сабпакеты — unit, корневые helper'ы — контейнерные (`tests/testkit/`).

## Инварианты и подводные камни

- **Без Docker = skip.** Условие проверяет сам helper (`require_docker`);
  дублировать его в тесте не нужно. Тестам, которые строят контейнер сами,
  тот же контракт даёт `testkit.require_docker()`.
- **Один контейнер на модуль тестов.** Старт Postgres/MinIO стоит секунды; держите
  фикстуру со `scope="module"` (или `loop_scope="module"` у pytest-asyncio), а
  не на каждый тест.
- **Пул фикстуры один на фикстуру.** `pool`/`db`/`transactor` отдают один и тот же
  пул с prod-таймаутами `pgkit` (`statement_timeout`, `lock_timeout`,
  `idle_in_transaction_session_timeout`) — код под тестом ведёт себя как под
  `pgkit.new_pool`. Пул закрывается на выходе из `start_postgres`.
- **testkit — только для тестов.** Не импортируйте его и сабпакеты в
  production-контейнер: он тянет testcontainers и существует ради проверки
  логики на портах китов.
- **Fake'и держат контракт боевой реализации.** Невалидный вход (пустой `ip`,
  `Entry` без `action`, несериализуемый payload) фейк отвергает так же, как
  pgstore/Bus — проводка, которая передаёт мусор, падает в юнит-тесте, а не на проде.
- **Теги образов конкретные.** `:latest` не используется, чтобы тесты не
  менялись от обновления registry; другой образ — `image=`.
