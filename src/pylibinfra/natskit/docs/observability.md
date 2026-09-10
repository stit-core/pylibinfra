# Observability natskit

Единый источник правды по телеметрии кита. Имена и labels — точно как в коде
(`instrumentation.py`, `bus.py`, `jetstream.py`, `conn.py`).

Meter и Tracer: scope `pylibinfra.natskit` (через `_internal.otelkit`).
Инструменты создаются на инстанс шины и транспорт (свой набор у `NatsBus` и у
`JsBus`; SDK склеивает их по имени в один инструмент). Без зарегистрированного
`MeterProvider`/`TracerProvider` сигналы — no-op.

## Метрики

| Метрика | Тип | Labels | Когда / что |
|---|---|---|---|
| `nats_messages_published_total` | counter | `instance`, `status`, `subject`**, `transport`* | +1 на каждую публикацию. `status` ∈ `ok` / `error`. |
| `nats_publish_duration_seconds` | histogram (`s`) | `instance`, `subject`**, `transport`* | Длительность публикации (Core — вокруг отправки; JetStream — до `PubAck`/ошибки/таймаута). Пишется и при `error`. |
| `nats_messages_consumed_total` | counter | `instance`, `subject`, `status`, `transport`* | +1 на каждое обработанное входящее сообщение. `status` ∈ `ok` / `handler_error` / `panic` / `dead` / `dlq_error`. |
| `nats_handle_duration_seconds` | histogram (`s`) | `instance`, `subject`, `transport`* | Длительность работы обработчика (включая `ack`/`nak`). Пишется при любом исходе. |
| `nats_inflight_messages` | up-down counter | `instance`, `subject`, `transport`* | Сообщения в обработке прямо сейчас: +1 на входе в обработчик, −1 на выходе. |

\* Label `transport` присутствует **только у JetStream-пути** (`JsBus`) со
значением `jetstream`. Core-путь этот label не ставит.

\*\* На publish-метриках label `subject` появляется, только если публикация
получила `metric_subject=` (или `StreamMsg.metric_subject`). Без него label
отсутствует: реальный subject публикации (`alerts.employee.<id>`) завёл бы серию
на каждый id.

Границы бакетов гистограмм `_seconds` — общие для всех китов
(`_internal.otelkit.DURATION_BUCKETS`): `.005 .01 .025 .05 .1 .25 .5 1 2.5 5 10 30`.

### Семантика label `instance`

`instance` — имя инстанса шины (`NatsProvider("hub")` → `hub`, дефолтный —
`default`). Приложение с несколькими шинами (своя + внешняя) иначе складывало бы
их сигналы в одну серию.

### Значения label `status`

| `status` | Где | Что значит |
|---|---|---|
| `ok` | publish + consume | Публикация прошла; обработчик вернулся без ошибки. JetStream при `ok` делает `ack`. |
| `error` | publish | Сбой публикации (транспорт, отказ JetStream, таймаут `PubAck`). |
| `handler_error` | consume | Обработчик поднял исключение (в т.ч. `TimeoutError` по `HandlerTimeout`). В JetStream → `nak`. |
| `panic` | consume | Обработчик завершился не-`Exception`-исключением: кит логирует стек. Core: сообщение теряется. JetStream: `nak` (если не исчерпан `MaxDeliver`). |
| `dead` | consume (только JetStream) | Сообщение больше не ретраится: `TerminalError` (включая ошибку декода в `subscribe_*_json`) **или** исчерпан `MaxDeliver`. DLQ-копия (если subject задан) сохранена, оригинал получил `term`. |
| `dlq_error` | consume (только JetStream) | Сообщение мёртвое, но DLQ-публикация не удалась: `term` не делается, оригинал возвращается на ределивери (`nak`). Серия > 0 — DLQ недоступен. |

### Семантика label `subject`

- publish — шаблон из `metric_subject=` (`alerts.employee.*`); без опции label не
  ставится вовсе.
- consume (Core и JetStream) — **subject подписки** (`subscribe(subject)` /
  `filter_subject` консьюмера), не реальный subject сообщения. Wildcard-подписка
  `orders.*.created` даёт одну серию, а не по серии на каждый id. Реальный
  subject обработчик читает из `subject_from_context()`; в спанах он есть.

Кардинальность серий = число подписок × инстансов.

## Спаны

| Спан | Kind | Имя | Атрибуты |
|---|---|---|---|
| Producer | `PRODUCER` | `natskit.publish <шаблон или subject>` | `messaging.system=nats`, `messaging.destination.name=<subject>`, `messaging.operation.type=send`, `messaging.message.body.size`, `instance`; при `metric_subject` — ещё `messaging.destination.template` |
| Consumer | `CONSUMER` | `natskit.handle <фильтр подписки>` | `messaging.system=nats`, `messaging.destination.name` = реальный subject, `messaging.destination.template=<фильтр подписки>`, `messaging.operation.type=process`, `messaging.message.body.size`, `instance` |

Имя consumer-спана — фильтр подписки, а не реальный subject: для
`orders.*.created` иначе получилось бы имя на каждый id.

- Producer-спан открывается перед публикацией, после чего W3C trace-context
  (`traceparent`/`tracestate`) инжектится в заголовки сообщения.
- Consumer-спан открывается как child producer-спана: контекст экстрактится из
  заголовков входящего. Так трасса остаётся связной даже через персист JetStream.
- Метрики пишутся с контекстом своего спана (publish — producer, consume —
  consumer): при включённых exemplar'ах из `nats_handle_duration_seconds` можно
  прыгнуть в конкретный трейс.
- На обоих концах при ошибке спан получает статус `ERROR` и запись исключения.

## Логи

Все записи — через `logkit.Logger` инстанса: `Conn`, `Bus` и `StreamBus`
добавляют к логгеру поле `instance`, поэтому оно есть в каждой записи кита.
Userinfo в поле `url` редактируется (`nats://xxxxx@host:4222`) — пароль и токен
из `NATS_URL` в логи не попадают.

| Уровень | Сообщение | Когда |
|---|---|---|
| `error` | `natskit: async error` | Асинхронная ошибка клиента: slow consumer (поля `subject`, `pending`), сбой протокола. |
| `warn` | `natskit: disconnected` | Разрыв соединения (не при `close`). Поле `url`. |
| `info` | `natskit: reconnected` | Переподключение; поле `url` (userinfo скрыт). |
| `error` | `natskit: connection closed, reconnects exhausted` | `NATS_MAX_RECONNECTS` исчерпан: соединение закрыто навсегда, подписки мертвы. Поля `url`, `err`. |
| `info` | `natskit: dlq subject is stored in a stream` | На старте: DLQ-subject накрыт стримом `stream`, копии уйдут JetStream-публикацией с `PubAck`. |
| `warn` | `natskit: dlq stream lookup` | Не удалось выяснить, накрыт ли DLQ-subject стримом; копии уйдут Core-публикацией. |
| `warn` | `natskit: stream config drift` | Конфиг существующего стрима расходится с ENV (поля `stream`, `field`, `want`, `have`); кит стрим не переписывает. |
| `warn` | `natskit: subscription drain` | Подписка не додренилась в бюджет `close` / ошибка снятия интереса. |
| `warn` | `natskit: drain timeout, handlers still in flight` | `close` исчерпал `NATS_DRAIN_TIMEOUT`, обработчики ещё работают (поле `inflight`). |
| `warn` | `natskit: drain timeout, closing connection` | Flush публикаций не уложился в остаток бюджета — соединение закрыто принудительно. |
| `warn` | `natskit: jetstream consume error` | Ошибка pull-цикла (консьюмер удалён, соединение закрыто); поля `stream`, `subject`, `durable`. |
| `warn` | `natskit: jetstream in-progress` | Не удалось отправить `in_progress` — сервер может переотправить сообщение раньше, чем обработчик закончит. |
| `error` | `natskit: handler error` / `natskit: jetstream handler error` | Обработчик поднял исключение (поля `subject`, `err`). |
| `error` | `natskit: handler panic` / `natskit: jetstream handler panic` | Обработчик завершился не-`Exception`-исключением, со стеком. |
| `warn` | `natskit: jetstream nak error` | Не удалось вернуть сообщение на ретрай; сервер переотправит его после `AckWait`. |
| `error` | `natskit: jetstream message dead` | Сообщение больше не ретраится (DLQ-копия сохранена, оригинал терминирован): поля `stream`, `durable`, `subject`, `deliveries`, `reason` (`terminal`/`max-deliver`), `dlq`, `err`. |
| `error` | `natskit: jetstream dlq publish failed, message kept for redelivery` | DLQ-публикация мёртвого сообщения не удалась: `term` не сделан, оригинал ушёл на ределивери. Поля те же плюс `err` (ошибка публикации) и `cause` (причина смерти). |
| `error` | `natskit: jetstream ack error` / `… term error` | Сбой подтверждения сообщения. |

## Health-probe

`natskit.probe(conn)` отдаёт `healthkit.Probe` с именем `natskit` (дефолтный
инстанс) или `natskit/<instance>` (именованный). Проверка зелёная при статусе
`connected`; после окончательного закрытия соединения — `ConnectionClosedError`
(с `last_error`), в промежуточных состояниях — ошибка со статусом
(`reconnecting`, `draining`).

`NatsProvider` пробу **не** provide'ит: в readiness имеет смысл класть не каждый
инстанс. Консьюмер кладёт её в кортеж `healthkit.Probes` своего App-Provider'а.

## Resource attributes

`natskit` **не** выставляет resource attrs (`service.name`, `service.version`,
`deployment.environment.name`) — их несёт OTel-resource, настроенный потребителем
(кит `telemetry` или собственная проводка OTel SDK). Кит читает meter/tracer и
propagator из глобальных провайдеров; без них сигналы — no-op.
