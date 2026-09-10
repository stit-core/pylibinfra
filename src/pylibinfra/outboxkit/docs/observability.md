# Observability outboxkit

Единый источник правды по сигналам кита. Meter и инструменты создаются через
`_internal.otelkit` под scope `pylibinfra.outboxkit`. Resource attrs и экспорт
настраивает потребитель в своей проводке OTel SDK; без зарегистрированного
`MeterProvider` инструменты — no-op.

Все метрики несут label `instance` — имя outbox-инстанса (`outbox` у
single-instance пути, `name` у `OutboxInstanceProvider`/`new_outbox`), чтобы
несколько outbox'ов в одном процессе не сливались в одну серию. Гистограммы
`_seconds` используют общие бакеты китов (`0.005 … 30`).

## Метрики (всегда)

| Метрика | Тип | Labels | Что считает |
|---|---|---|---|
| `outbox_events_inserted_total` | counter | `instance`, `subject`, `status` (`ok`\|`error`\|`marshal_error`) | Вставки события в outbox (`Bus.publish` → `Repository.insert`). |
| `outbox_events_processed_total` | counter | `instance`, `subject`, `status` (`published`\|`publish_error`\|`mark_error`) | Исход обработки события `Worker`'ом. |
| `outbox_events_dead_total` | counter | `instance`, `subject` | Переходы строки в dead (исчерпан `OUTBOX_MAX_ATTEMPTS`). |
| `outbox_revived_total` | counter | `instance` | Dead-строки, возвращённые в очередь `Outbox.revive_dead`. |
| `outbox_batch_size` | histogram | `instance` | Размер батча, вычитанного за тик. |
| `outbox_batch_duration_seconds` | histogram (`s`) | `instance` | Длительность всего тика (включая транзакцию). |
| `outbox_publish_duration_seconds` | histogram (`s`) | `instance` | Длительность фазы публикации в тике. |

Семантика `status` у `outbox_events_inserted_total`:

- `ok` — событие вставлено в outbox-таблицу;
- `error` — вставка не удалась (ошибка хранилища или tx-strict guard);
- `marshal_error` — payload не сериализуется в JSON; до хранилища не дошёл.

Семантика `status` у `outbox_events_processed_total`:

- `published` — событие подтверждено `PubAck`'ом, помечено published и тик
  закоммичен (счётчик эмитится после коммита);
- `publish_error` — публикация не удалась (строка уйдёт на ретрай после backoff
  или станет dead);
- `mark_error` — публикация прошла, но `mark_published` упал; транзакция тика
  откатилась, строка останется неопубликованной и переотправится (дедуп уберёт
  дубль).

Тик, пропущенный в `ordered`-режиме из-за занятой головы очереди
(`outboxkit.BusyError`), в `outbox_batch_size` не попадает — только в
`outbox_batch_duration_seconds`.

## Метрики (через BacklogReporter)

| Метрика | Тип | Labels | Что считает |
|---|---|---|---|
| `outbox_lag_seconds` | observable gauge (`s`) | `instance`, `subject` | Возраст самого старого неопубликованного (не-dead) события. |
| `outbox_pending_count` | observable gauge | `instance`, `subject` | Количество неопубликованных (не-dead) событий. |
| `outbox_backlog_errors_total` | counter | `instance` | Снимки backlog'а, завершившиеся ошибкой (gauge'и за этот сбор не эмитятся). |

Эти сигналы появляются, если репозиторий реализует `outboxkit.BacklogReporter`
(`backlog()`) — его реализует `pgstore.Store` — **и** наблюдение зарегистрировано:

- `OutboxProvider`/`OutboxInstanceProvider` делают это сами при сборке инстанса
  и снимают наблюдение на закрытии контейнера;
- вручную — `Outbox.register_backlog_gauges()` (или `register_backlog_gauges(name,
  reporter)` для голого `Repository`); вернувшийся handle нужно `unregister()` на
  shutdown. Хранилище без `BacklogReporter` даёт `None` — это не ошибка.

Снимок берётся синхронным callback'ом OTel, а `backlog()` асинхронный: корутина
исполняется на event-loop'е, захваченном в момент регистрации (его держит
lifecycle приложения). Оба gauge'я делят один снимок на цикл сбора — в хранилище
уходит один запрос, и `lag` с `pending` описывают одно и то же состояние очереди.
Сбой снимка не валит экспорт метрик процесса: gauge'и за этот сбор просто не
эмитятся, а факт сбоя виден в `outbox_backlog_errors_total` — алерт на lag должен
учитывать этот счётчик, иначе «нет отставания» и «снимок не берётся» выглядят
одинаково.

Сборщик метрик обычно работает в своём потоке (`PeriodicExportingMetricReader`),
и снимок берётся синхронно — значения свежие. Reader, который собирает метрики
**на том же event-loop'е** (pull-экспортёр внутри ASGI-хендлера, ручной
`get_metrics_data()` из async-кода), получает снимок предыдущего сбора, а
следующий заказывает фоновой таской: блокирующее ожидание сняло бы весь loop.

`unregister()` останавливает наблюдение инстанса; сам observable-инструмент
остаётся зарегистрированным в провайдере — OTel-Python не умеет его снимать.

## Спаны

Сам `outboxkit` спанов не открывает — `Worker` инструментирован только метриками.
Producer-спан публикации создаёт транспорт `natskit.StreamBus` (на
`publish_stream`/`publish_stream_batch`), тик целиком лежит под спаном
`timerkit.job outboxkit.publisher.<instance>`, а запросы к Postgres получают
per-query спаны от `pgkit`. Трасса доставки события прослеживается через спаны
`natskit` (producer) и потребителя.

Label `subject` publish-метрик `natskit` транспорт задаёт равным имени
outbox-инстанса (`metric_subject`): доменный subject в этом label дал бы
неограниченную кардинальность.

## Логи

Собственных записей у кита немного: ошибки тика логирует `timerkit` (он гоняет
`Outbox.job()`), ошибки публикации и poison-путь — `natskit`. Логгер инстанса
несёт поле `instance`.
