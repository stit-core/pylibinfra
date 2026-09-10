# Тестирование outboxkit

## Домен: фейковый Publisher

Доменный код зависит от `outboxkit.Publisher`, поэтому его тесты обходятся
`testkit.outboxtest.Recorder` — in-memory реализацией порта с `all()`/
`published(subject)`/`last(subject)`/`count()`/`reset()`. Ни Postgres, ни NATS не
нужны:

```python
recorder = outboxtest.new()
service = OrderService(orders_repo, recorder)

await service.create(order)

assert recorder.last("order.created") == {"id": order.id}
```

Фейк сериализует payload так же, как боевой `Bus`: несериализуемое значение
поднимает `outboxkit.OutboxError` — тест не пропустит событие, которое в проде не
доехало бы до БД.

## Core: фейки Repository / Transport

`Worker` зависит только от портов `Repository`, `Transport` и `pgkit.Transactor`,
поэтому его логика тестируется без БД и брокера — на in-memory фейках
(`testkit.pgtest.Identity`/`Recorder` как транзактор; они же снимают tx-strict
guard маркером `pgkit.with_test_tx`). Минимальный транспорт реализует два метода:

```python
class FakeTransport:
    async def publish(self, msg: outboxkit.OutMsg) -> None: ...
    async def publish_batch(self, msgs) -> list[Exception | None]: ...  # None = подтверждено
```

Так покрываются инварианты кита:

- тик — ровно одна транзакция (`pgtest.Recorder.calls() == 1`), режим публикации
  доезжает до `list_unpublished`, `mark_published` получает `MessageRef` с ключом
  партиционирования;
- `batch`: при частичном сбое метятся только подтверждённые строки, неудачные
  идут в failures; `ordered`: стоп на первой ошибке — метится только непрерывный
  префикс, хвост не публикуется;
- `outboxkit.BusyError` от репозитория — тихий пропуск тика без публикаций;
- `OUTBOX_TICK_TIMEOUT` обрывает зависшую публикацию, а отмена вызывающего не
  мешает пометить уже подтверждённые строки (фаза mark/commit идёт мимо отмены);
- ошибки хранилища (list / mark / record failures) доходят наружу обёрнутыми;
- poison: при `max_attempts > 0` строка, достигшая порога, помечается
  `Failure.dead`; при `0` dead не наступает никогда; пауза `retry_after` растёт
  экспоненциально и упирается в потолок;
- `last_error` режется до 1000 символов;
- счётчики `published`/dead эмитятся только после коммита тика, `mark_error` — на
  откате.

Метрики проверяются фикстурами `read_metric`/`metric_reader`: имена, атрибуты
(`instance`, `subject`, `status`) и общие бакеты гистограмм. Observable-gauge'и
backlog'а собираются из отдельного потока (`asyncio.to_thread(read_metric, …)`) —
так же, как их собирает `PeriodicExportingMetricReader` в проде.

## Транспорт: дедуп Msg-Id

`Nats-Msg-Id` проверяется на фейке `testkit.natstest.Recorder` (он реализует
`natskit.StreamBus`): `new_stream_transport(sb)` отдаёт голый id (`"42"`),
`new_stream_transport(sb, "orders")` — namespace'ный (`"orders:42"`), один id в
двух namespace'ах даёт разные `Msg-Id`, а `metric_subject` равен имени инстанса.

## pgstore: testcontainers

Интеграционные тесты поднимают Postgres через
`testkit.start_postgres(migrations=[pgstore.MIGRATIONS])` — миграции кита
применяются из его каталога, таблица инстанса — через
`pgstore.table_migrations("outbox_orders")`. Хелпер сам пропускает тест без
Docker. Покрывают:

- happy-path insert→list→mark (`published_at` ставит база), rollback (строка не
  остаётся в outbox при откате транзакции), tx-strict guard (`NoTxError` вне
  транзакции, успех внутри `with_tx`);
- backoff: строка с `next_attempt_at` в будущем не вычитывается в `batch`, а в
  `ordered` останавливает выборку на голове очереди; наступление срока возвращает
  её;
- poison и `revive_dead`: dead-строка исключается из выборки и из backlog'а,
  фильтр по subject возвращает её в очередь и сбрасывает `attempts`;
- backlog: возраст и число неопубликованных по каждому subject'у;
- блокировки: в `ordered` вторая транзакция получает `BusyError`, пока первая
  держит голову очереди, а `batch` в той же ситуации берёт следующие строки;
- партиционирование: таблица партиционирована (`relkind = 'p'`),
  `ensure_partitions` идемпотентна и считает месяцы от начала месяца
  (фиксированные часы `clockkit.Fixed` на 31 января);
- retention: старая пустая партиция дропается, партиция с неопубликованными
  остаётся (`PendingRowsError`), после публикации дропается и она;
- два конкурентных воркера в `batch`: каждое событие публикуется ровно один раз;
- таблица инстанса: `table_migrations` заводит её вместе с партициями и
  `next_attempt_at`;
- ошибки хранилища на отсутствующей таблице приходят обёрнутыми.

Чистые unit-проверки без контейнера: валидация имени таблицы (`Store` отвергает
пустое, заглавные, дефисы, SQL-инъекцию), нейминг партиций на датах 29–31 числа,
`schema_sql` в синхроне с миграциями, `maintenance_jobs`, отказ
`drop_old_partitions` внутри транзакции, генерация `table_migrations`.

## Команды

```bash
uv run pytest tests/outboxkit -m "not integration"   # быстрые тесты
uv run pytest tests/outboxkit                        # полный прогон (нужен Docker)
```
