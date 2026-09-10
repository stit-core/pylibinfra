# pgkit — observability

Единый источник правды по телеметрии кита. Пул инструментируется метриками
`pg_pool_*`, а запросы — спанами `opentelemetry-instrumentation-asyncpg`.
Метрики регистрирует `instrument_pool(pool, role)` через глобальный OTel Meter
(scope `pylibinfra.pgkit`); в dishka-графе это делает `PgProvider` для primary и
реплики, снимая регистрацию при закрытии контейнера.

Кит **не выставляет** resource-атрибуты
(`service.name`/`service.version`/`deployment.environment.name`) — их несёт
OTel-resource, настроенный потребителем. Без зарегистрированного
`MeterProvider` инструменты — no-op.

## Метрики `pg_pool_*`

Пять метрик, все снимаются в одном наборе observable-gauge'ей поверх статистики
пула. Значения — мгновенное состояние пула.

| Имя | Тип | Источник (`asyncpg.Pool`) | Назначение |
|---|---|---|---|
| `pg_pool_acquired_conns` | ObservableGauge | `get_size() - get_idle_size()` | соединения, занятые прямо сейчас |
| `pg_pool_idle_conns` | ObservableGauge | `get_idle_size()` | простаивающие соединения |
| `pg_pool_total_conns` | ObservableGauge | `get_size()` | всего открытых соединений |
| `pg_pool_max_conns` | ObservableGauge | `get_max_size()` | сконфигурированный лимит пула |
| `pg_pool_min_conns` | ObservableGauge | `get_min_size()` | сконфигурированный минимум пула |

Кумулятивных счётчиков acquire'ов у кита нет: asyncpg не ведёт статистику
`acquire_count` / `empty_acquire_count` / `canceled_acquire_count` /
`acquire_duration`, и брать их неоткуда. Сатурацию пула читают по
`pg_pool_acquired_conns / pg_pool_max_conns`, а время ожидания соединения — по
латентности запросов из спанов.

Гистограмм длительности кит не эмитит: латентность запроса несут спаны
otel-asyncpg.

### Labels

| Label | Значения | Откуда |
|---|---|---|
| `role` | `primary` \| `replica` | `PgProvider`: primary-пул и пул `PG_REPLICA_DSN`; для ручного `instrument_pool` — аргумент `role` (`pgkit.ROLE_PRIMARY`/`pgkit.ROLE_REPLICA` или своё значение) |

Пулы держатся в реестре по `role`: несколько пулов с одним `role` в одном
процессе схлопнутся в одну серию — давай им разные `role`. Закрытый пул
снимается с наблюдения через `PoolInstrumentation.unregister()` (в
dishka-графе — на закрытии контейнера).

### Чтение

- **Сатурация пула.** `pg_pool_acquired_conns / pg_pool_max_conns` близко к `1` —
  пул упирается в лимит; повышай `PG_MAX_CONNS` (сверяясь с `max_connections`
  сервера) или снижай длительность запросов.
- **Холодный пул.** `pg_pool_total_conns` держится на `pg_pool_min_conns` при
  растущей латентности запросов — соединения создаются на каждый всплеск;
  подними `PG_MIN_CONNS`.

## Спаны otel-asyncpg

При `PG_OTEL_TRACE=true` (default) один раз на процесс включается
`AsyncPGInstrumentor`: он патчит asyncpg глобально и даёт client-спаны на каждый
запрос (`SpanKind=client`, атрибут `db.system=postgresql`, scope
`opentelemetry.instrumentation.asyncpg`). Спаны наследуют текущий контекст и
встраиваются в общую трассу запроса.

| Спан | Когда |
|---|---|
| `<SQL>` | `execute`/`fetch`/`fetchrow`/`fetchval` и их аналоги на соединении |

**PII.** По умолчанию в спан идёт текст запроса, но **не** значения аргументов.
Литералы внутри SQL при этом попадают в спан целиком — персональные данные
передавай аргументами (`$1`), а не подстановкой в текст.

Выключение `PG_OTEL_TRACE` убирает спаны (метрики `pg_pool_*` от него не зависят
и продолжают эмититься). Инструментация глобальная: один выключенный пул её не
снимет, если её включил другой.

## Health-пробы

| Проба | Имя | Проверка |
|---|---|---|
| `pgkit.probe(db)` | `pgkit` | `SELECT 1` на пуле primary |
| `pgkit.replica_probe(rw)` | `pgkit/replica` | `SELECT 1` на пуле реплики; без реплики проходит без запроса |

Обе кладутся в `healthkit.Probes` App-Provider'ом консьюмера и попадают в
`/ready`; ограничение времени одной проверки — `healthkit.with_timeout`. Ошибка
пробы префиксуется именем кита (`pgkit: ping: ...`).

## Логи

Своего логгера кит не заводит: ошибки поднимаются исключениями и логируются
вызывающим слоем. Текст исключения безопасно логировать целиком — пароль из
`PG_DSN` кит вырезает до того, как сообщение драйвера попадёт в ошибку
(`pgkit.redact_dsn` доступен и для собственных логов потребителя).

## Источник правды по реестру

Сводка по всем китам — корневой [README § Что эмитит каждый пакет](../../../../README.md):
`pgkit` → `pg_pool_*` (5 метрик, label `role`) + спаны otel-asyncpg. Эта
страница — детальный реестр имён, типов и labels для `pgkit`.
