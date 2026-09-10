# Архитектура natskit

## Назначение и место в стеке

`natskit` — транспортный слой поверх NATS для сервисов stit. Он покрывает две
модели доставки и прячет их за минимальной поверхностью:

1. **Core NATS** (`Bus`) — fire-and-forget pub/sub без персиста. Подходит для
   эфемерных уведомлений, где допустима потеря при отсутствии живого подписчика.
   Подписка бывает fan-out (`subscribe` — каждому инстансу) и load-balanced
   (`subscribe_queue` — одному из queue-группы).
2. **JetStream** (`StreamBus`) — персист и at-least-once доставка через
   durable-консьюмеров с explicit-ack, дедуп публикации по `Nats-Msg-Id`,
   poison-обработка (DLQ / `MaxDeliver` / NAK-backoff). Для событий, которые
   нельзя терять.

Поверх обеих моделей кит даёт: типизированные JSON-хелперы, recovery упавшего
обработчика, backpressure семафором, handle подписки (`Subscription`), graceful
`close()` с ожиданием обработчиков, W3C trace-propagation и метрики `nats_*`.

Доменное — вне кита: схемы сообщений, идемпотентность по бизнес-ключу,
retry-политика бизнес-уровня, выбор subject'ов и стримов.

## Компоненты

| Модуль | Роль |
|---|---|
| `bus.py` | `Bus`-протокол (`publish`/`subscribe`/`subscribe_queue`), `NatsBus` (Core), `invoke_handler` (deadline), дженерики `publish_json`/`subscribe_json`/`subscribe_queue_json`, `decode_json`. |
| `publish.py` | `PublishSpec` (msg_id, headers, metric_subject), `new_publish_spec`, `StreamMsg`. |
| `jetstream.py` | `StreamBus`-протокол, `JsBus`, `DisabledStreamBus`, `ensure_streams`/`_reconcile_stream`, deadline + `in_progress`, poison-путь (`_fail_stream`/`_dead_letter`), заголовки DLQ, `parse_retention`. |
| `subscription.py` | `Handler`, `Subscription` (`unsubscribe`/`drain`), `TrackedSubscription` и `InflightGroup` — учёт работающих обработчиков. |
| `conn.py` | `Conn` — соединение и реестр подписок; `new_conn` (аутентификация, колбэки nats-py → `logkit`), `healthy`, graceful `close`. |
| `client.py` | `Client` — инстанс шины (соединение + `Bus` + `StreamBus`), `new_client`, `probe` (`healthkit.Probe`). |
| `config.py` | `Config` / `BackpressureConfig` / `JetStreamConfig` — публичный env-контракт; `new_config`, `new_config_prefixed`, `redact_url`. |
| `context.py` | `subject_from_context` / `headers_from_context` / `message_context` — метаданные входящего сообщения внутри обработчика. |
| `instrumentation.py` | `NatsInstruments` (5 otel-инструментов через `_internal.otelkit`), producer/consumer-спаны, инжект/экстракт trace-context в заголовках. |
| `subjects.py` | `subject_matches` и валидаторы имён (subject публикации, стрим, инстанс). |
| `module.py` | `NatsProvider` — dishka-проводка инстанса. |

## Bus (Core NATS)

```
publish(subject, payload, msg_id=, headers=, metric_subject=)
   │  start_publish_span: producer-спан + заголовки опций + inject trace-context
   ▼
nc.publish(...) ──► nats_messages_published_total{status[,subject]}
                    nats_publish_duration_seconds[{subject}]

subscribe / subscribe_queue ──► Subscription (unsubscribe / drain)
   │  колбэк на каждое входящее сообщение
   ▼
sem.acquire() ──► TrackedSubscription.spawn(...)   (не больше NATS_MAX_IN_FLIGHT)
   │  inflight +1
   │  start_consumer_span: extract trace-context → child-спан; subject+headers в contextvars
   │  deadline NATS_HANDLER_TIMEOUT (asyncio.wait_for)
   ▼
handler(payload)
   │  исключение → status=handler_error (лог)
   │  BaseException → status=panic (лог со стеком)
   ▼
nats_messages_consumed_total{subject,status}; nats_handle_duration_seconds; inflight −1
```

`subscribe` vs `subscribe_queue` — разница только в NATS-примитиве: fan-out
(каждому инстансу) против queue-группы (ровно одному участнику).

Core NATS не подтверждает доставку: при ошибке обработчика сообщение **не**
переотправляется — оно уже сконсюмлено. Нужны гарантии и ретраи — это JetStream.

Обработчик ограничен `NATS_HANDLER_TIMEOUT` (30s по умолчанию): по истечении
`asyncio.wait_for` отменяет таску обработчика, кит пишет `natskit: handler error`
с текстом `handler timeout after …` и освобождает слот семафора. Отмена приходит
и от `close()`, когда исчерпан бюджет drain.

## StreamBus (JetStream)

`JsBus` активен только при `NATS_JETSTREAM_ENABLED=true`; иначе `new_stream_bus`
возвращает `DisabledStreamBus`, все методы которого поднимают
`JetStreamDisabledError` — DI-граф собирается, а неверное использование
выключенного транспорта становится явной ошибкой, а не падением по `None`.

### Декларация стримов

При старте `ensure_streams` проходит по `NATS_JS_STREAM_NAMES`. Режим задаёт
`NATS_JS_STREAM_MANAGED`:

- `true` — отсутствующий стрим создаётся целиком (subjects `<name>.>`,
  file-storage, retention/`max_age`/`duplicates` из конфига; окно дедупа
  поджимается до `max_age` — требование JetStream). У существующего стрима
  сверяются только subjects: `<name>.>` доливается объединением, остальные поля
  (`num_replicas`, `max_bytes`, `max_age`, чужие subjects) не трогаются, а
  расхождение с ENV сообщается записью `natskit: stream config drift`. Гонка двух
  инстансов на создании уходит в ту же сверку subjects.
- `false` — кит только проверяет, что каждый стрим существует; иначе `StreamError`
  на старте.

### Публикация

- `publish_stream` — синхронно: публикация и ожидание `PubAck` в пределах
  `NATS_JS_PUBLISH_ASYNC_TIMEOUT`.
- `publish_stream_batch` — async: все сообщения уходят `publish_async`, затем
  метод ждёт исход **каждого своего** future'а (`results[i] is None` — персист
  подтверждён). Ожидание пер-future, а не «пока опустеет общий pending»: потерянный
  `PubAck` соседнего батча иначе задерживал бы этот на весь таймаут. Порядок
  персиста best-effort: при частичном сбое неподтверждённые уходят на ретрай и
  могут получить более поздний sequence — дедуп по `Nats-Msg-Id` гарантирует
  отсутствие дублей. Незавершившийся future не отменяется: поздний ack просто
  отбрасывается, а сообщение получает `PublishError: pub ack timeout` — на это
  опирается `outboxkit`.

`msg_id` (или `StreamMsg.msg_id`) ставит `Nats-Msg-Id`: в пределах окна
`NATS_JS_DUPLICATES` повторная публикация с тем же id дедуплицируется стримом
(effectively-once персист при ретраях продьюсера).

### Consumer reliability

`subscribe_stream(stream, subject, durable, handler)` создаёт (или обновляет)
durable pull-консьюмера с `AckPolicy.EXPLICIT`, `filter_subject=subject`,
`ack_wait=NATS_JS_ACK_WAIT`, `max_ack_pending=NATS_JS_MAX_IN_FLIGHT` и запускает
цикл `fetch`; ошибки цикла (консьюмер удалён, нет соединения) логируются
(`natskit: jetstream consume error`) и не роняют подписку. Обработка сообщения:

```
sem.acquire() ──► spawn(handle_one)
   │ inflight +1
   │ consumer-спан + deadline NATS_JS_HANDLER_TIMEOUT
   │ keep_in_progress: каждые AckWait/2 → msg.in_progress(), пока handler работает
   ▼
handler(payload): исключение → err; BaseException → panic
   │
   ├─ успех ───────────────► msg.ack()            status=ok
   └─ ошибка/panic ────────► fail_stream(...)
                                │
            TerminalError в цепочке или deliveries ≥ MaxDeliver?
                 ├─ да  → dead_letter (если DLQ-subject задан)
                 │        ├─ копия ушла → log error + msg.term()   status=dead
                 │        └─ отказ DLQ  → log error + nak          status=dlq_error
                 └─ нет → nak(delay=NakBackoff) | nak()   status=handler_error|panic
```

- **Deadline + `in_progress`.** Сервер переотправляет сообщение, не дождавшись
  `ack` за `AckWait`; без продления живой, но медленный обработчик получал бы
  конкурентный ределивери. Фоновый keepalive сдвигает таймер сервера, пока
  обработчик работает, а `NATS_JS_HANDLER_TIMEOUT` (по умолчанию равен `AckWait`)
  — дедлайн самой попытки: зависший обработчик получает `CancelledError`,
  сообщение идёт по poison-пути.
- **`TerminalError`** — обработчик заявляет, что ретрай бессмыслен (чужая схема,
  битый payload): кит идёт в DLQ/`term` с первой попытки. Тем же маркером
  обёрнута ошибка декода `subscribe_*_json`. Проверка идёт по цепочке
  `raise … from` (`natskit.is_terminal`).
- **`MaxDeliver`** — потолок числа доставок. Ставится и на консьюмере
  (server-side backstop), и проверяется клиентом перед `term`. `0` — без лимита.
- **NAK-backoff** — при `NakBackoff > 0` сообщение возвращается с задержкой,
  иначе мгновенно. Задержка распределяет ретраи во времени, не упирая консьюмера
  в горячую петлю.
- **DLQ** — на терминальном отказе копия republish'ится в `NATS_JS_DLQ_SUBJECT`
  с заголовками `Natskit-Dlq-*` и остальными исходными заголовками. `Nats-Msg-Id`
  в копию не переносится (иначе стрим с окном дедупа выбросил бы её как дубль),
  `Nats-Expected-*` не копируются (они адресованы исходному стриму). Публикация
  идёт через JetStream, если DLQ-subject накрыт стримом (решается один раз на
  старте), иначе Core-публикацией с `flush`. Сам отказ — всегда запись
  `natskit: jetstream message dead`, поэтому и без DLQ причина не теряется.
  Причина смерти уезжает в заголовок `Natskit-Dlq-Error` и в лог: `term` идёт без
  текста — nats-py не умеет `+TERM <reason>`.
- **Фильтр не должен накрывать DLQ-subject.** `subscribe_stream` проверяет это на
  регистрации и поднимает `ConfigError`: иначе DLQ-копия вернулась бы тому же
  обработчику и цикл «отказ → копия → отказ» рос бы в стриме без остановки.
- **Граница DLQ.** Копия появляется, только если последняя доставка провалилась в
  живом процессе. Умерший процесс и истёкший `AckWait` на последней доставке
  оставляют сообщение неподтверждённым: сервер больше не доставляет его, и
  клиенту нечего дед-леттерить.

`durable` — имя durable-консьюмера: переподключение продолжает с того же места.
Несколько инстансов с одним `durable` делят нагрузку (JetStream-аналог
queue-группы).

## Subscription и graceful shutdown

Каждая подписка регистрируется в `Conn` и считает свои работающие обработчики
(`InflightGroup`; тот же счётчик есть у `Conn` целиком).

- `Subscription.unsubscribe()` — снимает интерес сразу; уже полученные, но не
  начатые сообщения не обрабатываются (JetStream переотправит их после
  `AckWait`), работающие обработчики не ждутся.
- `Subscription.drain(timeout=…)` — снимает интерес, даёт дообработать буфер и
  ждёт завершения обработчиков в пределах бюджета; исчерпан — `TimeoutError`.
  Недодренившаяся подписка остаётся в реестре `Conn`: отменить её обработчики
  сможет только `close()`.
- `Conn.close(timeout=…)` с бюджетом `min(NATS_DRAIN_TIMEOUT, timeout)`: drain
  всех подписок → ожидание обработчиков → `drain` соединения (flush публикаций).
  Порядок важен: обработчики завершаются по живому соединению и успевают
  `ack`/`nak`. По истечении бюджета — предупреждение в лог, отмена работающих
  обработчиков и принудительное закрытие. После начала `close` новые подписки
  получают `ClosedError`.

Бюджет накрывает и сам транспортный drain: подписки дренятся параллельно, каждая
в пределах остатка бюджета. Без этого delivery-цикл, вставший на исчерпанном
`MaxInFlight`, держал бы `close()` столько, сколько живёт зависший обработчик.
Не уложившаяся подписка даёт запись `natskit: subscription drain`.

Счётчик обработчиков — не `asyncio.gather` по фиксированному списку: транспорт
может доставить сообщение уже во время drain'а, и счётчик обязан это терпеть.

## Trace-propagation

W3C trace-context (`traceparent`/`tracestate`) ездит вместе с сообщением в
заголовках NATS:

- producer (`start_publish_span`) открывает спан kind=`producer` и инжектит
  контекст в заголовки публикуемого сообщения;
- consumer (`start_consumer_span`) экстрактит контекст из заголовков входящего и
  стартует спан kind=`consumer` как child producer-спана — даже через персист
  JetStream трасса остаётся связной.

`subject_from_context()` / `headers_from_context()` дают обработчику доступ к
конкретному subject (для wildcard-подписок вроде `orders.*`) и к заголовкам
(`event-type`, `schema-version`) — без дублирования их в payload.

## Backpressure

И `NatsBus`, и `JsBus` держат `asyncio.Semaphore`. Каждое входящее сообщение
обрабатывается отдельной таской, но не больше `MaxInFlight` одновременно. При
исчерпании семафора delivery-цикл подписки блокируется:

- Core: буфер подписки конечен; переполнение — `slow consumer`, сообщения
  теряются, клиент сообщает об этом колбэком → лог `natskit: async error`.
- JetStream: `max_ack_pending = MaxInFlight` — потолок на **консьюмера**, а
  семафор один на все подписки инстанса; pull-цикл просто не запрашивает новый
  батч, пока слоты заняты.

## Соединение

`new_conn` собирает опции аутентификации (`NATS_CREDS_FILE`,
`NATS_NKEY_SEED_FILE`, `NATS_TLS_CA_FILE`) и вешает колбэки, которые пишут в
`logkit.Logger`: асинхронные ошибки клиента (в т.ч. slow consumer), disconnect,
reconnect, closed. `NATS_DRAIN_TIMEOUT` пробрасывается и в nats-py, чтобы его
внутренний drain укладывался в тот же бюджет.

Первичное подключение ограничено `NATS_CONNECT_TIMEOUT` на каждый сервер из
`NATS_URL`: nats-py с `max_reconnect_attempts=-1` иначе повторяет попытки
бесконечно и держит старт сервиса.

Любой логируемый URL проходит `redact_url`: userinfo (пароль или токен из
`NATS_URL`) заменяется на `xxxxx`. Закрытие соединения вне `close()` означает
исчерпание reconnect'ов — это запись уровня `error`, а `healthy()` с этого
момента отдаёт `ConnectionClosedError`, отличимый от временного `reconnecting`.

## dishka-проводка

`NatsProvider()` provide'ит `Client`, `Conn`, `Bus` и `StreamBus` одного инстанса
и закрывает соединение на выходе из контейнера (graceful `close`).

Пустой конструктор — дефолтный инстанс (`Config` из голых `NATS_*`). Именованный
инстанс живёт в своём dishka-компоненте: `NatsProvider("hub", component="hub")`
плюс провайдер с `natskit.new_config_prefixed("hub")` в том же компоненте.
`Logger` при этом берётся из дефолтного компонента, так что второй логгер не
нужен. Рецепт целиком — [usage.md](usage.md).

Имя инстанса ограничено `[A-Za-z0-9_.-]`: из него собирается ENV-префикс, и имя
вроде `hub/v2` дало бы переменные, которые невозможно задать. `NatsProvider` и
`new_config_prefixed` отвергают такое имя `ConfigError`.

Пробу кит **не** provide'ит: в readiness имеет смысл класть не каждый инстанс.
Регистрация — `natskit.probe(conn)` в кортеже `healthkit.Probes` консьюмера.

## Ключевые проектные решения (WHY)

- **Два интерфейса, не один.** `Bus` (Core) и `StreamBus` (JetStream) разнесены:
  у них разные гарантии (fire-and-forget vs at-least-once) и разная семантика
  ошибки обработчика (drop vs ретрай/DLQ). Один интерфейс скрыл бы эту разницу.
- **`DisabledStreamBus` вместо `None`.** При выключенном JetStream граф
  собирается, а неверное использование транспорта — явная ошибка в рантайме.
- **Стримы: создать или долить subject, не переписывать.** Полный апдейт из
  конфига кита затирал бы ручной тюнинг оператора (`num_replicas`, `max_bytes`,
  доп. subjects). Кит владеет только своим subject `<name>.>`.
- **`HandlerTimeout` отдельно от `AckWait`.** `AckWait` — про смерть процесса (за
  сколько сообщение уйдёт другому инстансу), бюджет обработки — про зависший код.
- **Дедуп — на стороне стрима, по `Nats-Msg-Id`.** Кит не хранит состояние
  публикаций; effectively-once персист обеспечивает сам JetStream в окне
  `Duplicates`. На это опирается `outboxkit` (namespaced `Nats-Msg-Id`).
- **`term` — только после успешной копии.** Провал DLQ-публикации оставляет
  сообщение в стриме (`nak`, `status=dlq_error`): недоступный DLQ иначе стирал бы
  данные молча. Цена — ретраи продолжаются, пока DLQ не оживёт; при
  `MaxDeliver > 0` их обрывает server-side backstop.
- **Backpressure обязателен.** Без семафора всплеск входящих породил бы
  неограниченное число тасок-обработчиков и съел бы память.
- **Упавший обработчик не роняет процесс.** Обработчик исполняется в отдельной
  таске, исключение логируется и учитывается метрикой. В Core сообщение при этом
  теряется; в JetStream — идёт по poison-пути.
- **Drain с бюджетом, не бесконечный.** Shutdown обязан завершиться: оркестратор
  всё равно убьёт процесс по своему таймауту.
- **`Config` не в провайдере.** Симметрично остальным китам: env-контракт
  регистрирует приложение, кит его только потребляет.
- **Свой префиксный загрузчик ENV.** `new_config_prefixed(name)` заполняет все
  поля явно: pydantic-settings иначе подхватил бы голую `NATS_*`, и именованный
  инстанс молча унаследовал бы URL и стримы дефолтного — то есть ушёл бы
  публиковать в чужую шину.
- **Валидация при чтении конфига.** Пустой `NATS_URL` молча подключил бы сервис к
  `nats://localhost:4222`, а `NATS_JS_ACK_WAIT=0.1` сломал бы keepalive на первом
  сообщении. Конфиг проверяется целиком, и сервис падает на старте с `ConfigError`.
