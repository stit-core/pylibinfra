# pgkit

Фасад поверх [asyncpg](https://github.com/MagicStack/asyncpg): пул соединений с
сессионными таймаутами, `DB` для запросов, `Transactor` с проброской транзакции
через `contextvars`, migrate-runner (advisory-lock + независимые источники
миграций поверх [yoyo](https://ollycope.com/software/yoyo/)), маппинг pg-ошибок
в булевы предикаты, health-пробы (`healthkit.Probe`) и опциональная read-replica
(`ReadWriteDB`). Из коробки: метрики `pg_pool_*` (label `role`) через глобальный
OTel Meter и per-query спаны через `opentelemetry-instrumentation-asyncpg`.

Kit даёт сервису соединение, надёжность пула и атомарность записи; SQL, схема и
доменные репозитории — на стороне потребителя.

## Импорт

```python
from pylibinfra.pgkit import DB, PgMigrateProvider, PgProvider, Transactor
```

Версия и установка — см. [корневой README § Использование в проекте](../../../README.md).

## Быстрый старт

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import healthkit
from pylibinfra.pgkit import (
    DB,
    Migrated,
    MigrateRunnerConfig,
    MigrationSources,
    OptionalReplicaConfig,
    PgMigrateProvider,
    PgProvider,
    PoolConfig,
    ReadWriteDB,
    Transactor,
    new_migrate_runner_config,
    new_pool_config,
    new_replica_config,
    probe,
    replica_probe,
)


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def pool_config(self) -> PoolConfig:
        return new_pool_config()  # PG_DSN и остальные PG_*

    @provide
    def replica_config(self) -> OptionalReplicaConfig:
        return new_replica_config()  # PG_REPLICA_DSN; без реплики провайдер не нужен

    @provide
    def migrate_config(self) -> MigrateRunnerConfig:
        return new_migrate_runner_config()  # PG_MIGRATIONS_PATH, PG_MIGRATE_TIMEOUT

    @provide
    def migration_sources(self) -> MigrationSources:
        return ()  # сюда кладутся <kit>.pgstore.MIGRATIONS

    @provide
    def probes(self, db: DB, rw: ReadWriteDB) -> healthkit.Probes:
        return (probe(db), replica_probe(rw))


container = make_async_container(PgProvider(), PgMigrateProvider(), AppProvider())

await container.get(Migrated)  # схема применена до приёма трафика
db = await container.get(DB)
transactor = await container.get(Transactor)


async def create_user(email: str) -> None:
    async def write() -> None:
        await db.execute("INSERT INTO users (email) VALUES ($1)", email)

    await transactor.with_tx(write)  # исключение или отмена → ROLLBACK
```

Минимальный ENV: `PG_DSN`. Провайдеры кита свои Config'и не отдают — их
регистрирует caller (`new_pool_config`/`new_replica_config`/
`new_migrate_runner_config` либо App-Provider), и каждый валидирует прочитанное
(`pgkit.ConfigError`). Фабрика `OptionalReplicaConfig` нужна только там, где есть
реплика: без неё `PgProvider` берёт свой дефолт «реплики нет». Запрос внутри
`with_tx` атомарно идёт в транзакцию из contextvar, вне неё — в пул.

> Прямые конструкторы (`new_pool`, `DB`, `PoolTransactor`, `ReadWriteDB`)
> доступны для тестов и не-DI-кода, но менее идиоматичны.

## Документация

- [docs/architecture.md](docs/architecture.md) — устройство пула, `DB`-фасада,
  `Transactor`, migrate-runner'а (advisory-lock + таблица версий на источник),
  read-replica и маппинга ошибок; проектные решения (WHY).
- [docs/configuration.md](docs/configuration.md) — полный справочник ENV
  (`PoolConfig` + `ReplicaConfig` + `MigrateRunnerConfig`) и тюнинг пула.
- [docs/observability.md](docs/observability.md) — метрики `pg_pool_*`, label
  `role`, спаны otel-asyncpg, health-пробы (единый источник правды).
- [docs/usage.md](docs/usage.md) — рецепты: dishka-проводка, транзакции,
  обработка pg-ошибок, миграции (киты + приложение), read-replica, пробы.
- [docs/testing.md](docs/testing.md) — интеграционные тесты через
  `testkit.start_postgres`, фейки `testkit/pgtest`, проверка метрик пула.

## Инварианты

- Нули размеров и таймаутов `PoolConfig` означают дефолт asyncpg, а не
  «выключено»: конфига с одним `PG_DSN` достаточно для рабочего пула.
- Конфиг валидируется при создании: нарушение инварианта валит старт
  (`pgkit.ConfigError`), а не первый запрос.
- Ошибка `fn` из `with_tx` не оборачивается; сорвавшийся откат добавляется к ней
  примечанием (`add_note`) и никогда не подменяет причину.
- Миграции идут под advisory-lock'ом: из одновременно стартующих инстансов
  применяет ровно один. Дедлайн ожидания у прямого `migrate_up_all` ставит
  вызывающий, у `PgMigrateProvider` — `PG_MIGRATE_TIMEOUT`.
- Ресурс, которому нужна схема, зависит от `pgkit.Migrated`, а не от порядка
  регистрации провайдеров.
- Пароль из `PG_DSN` не попадает в текст ошибки кита: сообщения драйверов
  редактируются (`pgkit.redact_dsn` — та же функция для собственных логов
  потребителя).
- Кит не ставит на пул json/jsonb-кодек: колонки этих типов приходят текстом.
  Разбор — `pgkit.unmarshal_jsonb` или `json.loads` на стороне потребителя.
