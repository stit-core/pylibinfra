# pgkit — тестирование

Интеграционные тесты поднимают реальный Postgres в контейнере через
`testkit.start_postgres`. Unit-уровень (предикаты ошибок, роутинг `ReadWriteDB`,
валидация конфигов и источников миграций, маппинг конфига в asyncpg) тестируется
без контейнера.

## `testkit.start_postgres`

```python
async with start_postgres(
    migrations: Sequence[pgkit.Source] = (),
    migrations_dir: str | None = None,
    image: str = testkit.POSTGRES_IMAGE,
) as pg: ...
```

Поднимает `testkit.POSTGRES_IMAGE` на свободном порту, ждёт готовности и гасит
контейнер на выходе. Без Docker `require_docker()` пропускает тест.
`migrations` применяются через `pgkit.migrate_up_all` — так же, как это делает
`PgMigrateProvider`; `migrations_dir` добавляется последним источником
`pgkit.AppSourceName`.

```python
class PostgresFixture:
    dsn: str
    container: PostgresContainer

    def pool_config(self) -> pgkit.PoolConfig
    async def pool(self) -> asyncpg.Pool
    async def db(self) -> pgkit.DB
    async def transactor(self) -> pgkit.Transactor
```

`pool()`, `db()` и `transactor()` работают поверх одного пула фикстуры,
открытого через `pgkit.new_pool` с prod-таймаутами (session-таймауты,
otel-asyncpg); пул закрывается вместе с контейнером.

## Рецепт: один контейнер на модуль

Контейнер и пул живут дольше одного теста, поэтому фикстура и тесты делят
event-loop модуля:

```python
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg() -> AsyncIterator[PostgresFixture]:
    async with start_postgres(migrations=[MIGRATIONS]) as fixture:
        yield fixture


async def test_repo(pg: PostgresFixture) -> None:
    db = await pg.db()
    assert await db.fetchval("SELECT 1") == 1
```

## Рецепт: миграции из временного каталога

`Source` принимает любой каталог, поэтому источник собирается из `tmp_path`:

```python
directory = tmp_path / "migrations"
directory.mkdir()
(directory / "0001_foo.sql").write_text("CREATE TABLE foo (id INT PRIMARY KEY);")
(directory / "0001_foo.rollback.sql").write_text("DROP TABLE foo;")

src = Source(name="foo", path=str(directory))
async with start_postgres(migrations=[src]) as pg:
    assert migrate_version(pg.dsn, src) == 1
    migrate_down(pg.dsn, src)
```

`migrate_*` синхронные — в async-тесте зови их через `asyncio.to_thread`, если
не хочешь блокировать loop.

## Рецепт: проверка метрик пула

`instrument_pool` пишет в глобальный Meter; общая фикстура `metric_reader`
(`tests/conftest.py`) ставит in-memory reader ДО создания пула:

```python
async def test_metrics(pg: PostgresFixture, read_metric) -> None:
    pool = await new_pool(pg.pool_config())
    registration = instrument_pool(pool, ROLE_PRIMARY)
    try:
        assert read_metric("pg_pool_max_conns", {"role": "primary"}) == 5
    finally:
        registration.unregister()
        await pool.close()
```

`unregister()` обязателен: реестр метрик переживает тест, и закрытый пул иначе
попал бы в следующий сбор.

## Рецепт: домен без Postgres

Фейки `pgkit.Transactor` живут в `testkit/pgtest`: `pgtest.Identity` прогоняет
`fn` без транзакции, `pgtest.Recorder` дополнительно считает вызовы. Оба ставят
маркер `pgkit.with_test_tx`, поэтому код, гейтящийся на `pgkit.in_tx`
(например outbox-insert), проходит и без БД:

```python
service = Service(repo, pgtest.Identity())
await service.do()
```

`pgtest.in_rollback_tx(pool)` — обратный случай: тело теста идёт в реальной
транзакции на пуле, которая откатывается на выходе, поэтому база после теста
остаётся чистой.

## Запуск

```bash
uv run pytest -m "not integration"   # unit: без Docker
uv run pytest                        # integration: поднимает Postgres (нужен Docker)
```
