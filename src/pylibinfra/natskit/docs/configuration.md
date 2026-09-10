# Конфигурация natskit

`natskit.Config` (с вложенными `BackpressureConfig` и `JetStreamConfig`) —
публичный env-контракт. Читается `natskit.new_config()` (голые `NATS_*`) либо
`natskit.new_config_prefixed(name)` (`<NAME>_NATS_*` — конфиг именованного
инстанса); оба валидируют результат и поднимают `natskit.ConfigError` на
невалидном значении. Конфиг в DI-граф кладёт приложение — `NatsProvider` его
только потребляет. Имя инстанса из ENV не читается: его задаёт `NatsProvider(name)`
/ `new_client(name, …)`, и оно едет атрибутом `instance` в метрики, спаны и логи.

> **Public API.** Имена полей и их `validation_alias` — часть стабильного
> контракта. Менять их = breaking change: выпускается minor-релизом, помечается
> `!` в Conventional Commit и строкой миграции в аннотации тега (см. [корневой
> README](../../../../README.md) § Версионирование).

**Заданная, но пустая переменная равна незаданной** — берётся дефолт кита.
`NATS_MAX_RECONNECTS=` в `.env`, Helm с `value: ""` и compose с незаданной
подстановкой дают именно такую переменную; без этого правила она молча обнуляла
бы безопасный дефолт (ни одной попытки reconnect'а, ретрай без backoff'а).
Непустое, но неразбираемое значение (`NATS_JS_ACK_WAIT=abc`) — `ConfigError` при
чтении конфига. Все длительности — **секунды** (`float`).

## Соединение и Core NATS

| Env | Поле | Тип | Default | Назначение |
|---|---|---|---|---|
| `NATS_URL` | `url` | `str` | `nats://localhost:4222` | Адрес NATS (или список через запятую). «Шов» под split-топологию — разное значение = разный хост. Схемы: `nats`, `tls`, `ws`, `wss`. |
| `NATS_CONNECT_TIMEOUT` | `connect_timeout` | `float, секунды` | `5` | Таймаут первичного подключения на каждый сервер списка; он же ограничивает старт при бесконечных reconnect'ах. |
| `NATS_RECONNECT_WAIT` | `reconnect_wait` | `float, секунды` | `2` | Пауза между попытками переподключения. |
| `NATS_MAX_RECONNECTS` | `max_reconnects` | `int` | `-1` | Максимум попыток переподключения после разрыва. `-1` — бесконечно (дефолт): NATS-maintenance любой длины переживается без потери шины. `>= 0` — потолок, после которого соединение закрывается навсегда (см. [Исчерпание reconnect'ов](#исчерпание-reconnectов)). Меньше `-1` — `ConfigError`. |
| `NATS_DRAIN_TIMEOUT` | `drain_timeout` | `float, секунды` | `10` | Общий бюджет `close()`: drain подписок + ожидание обработчиков + flush публикаций. По истечении обработчики отменяются, соединение закрывается принудительно. `0` → `10`, отрицательное — `ConfigError`. |
| `NATS_HANDLER_TIMEOUT` | `handler_timeout` | `float, секунды` | `30` | Бюджет одного Core-обработчика. `0` — без ограничения (зависший обработчик держит слот `MaxInFlight` до конца процесса), отрицательное — `ConfigError`. Для JetStream действует `NATS_JS_HANDLER_TIMEOUT`. |

## Аутентификация

| Env | Поле | Тип | Default | Назначение |
|---|---|---|---|---|
| `NATS_CREDS_FILE` | `creds_file` | `str` | `` (пусто) | Путь к `.creds`-файлу (NATS JWT + nkey seed). |
| `NATS_NKEY_SEED_FILE` | `nkey_seed_file` | `str` | `` (пусто) | Путь к файлу nkey-seed. |
| `NATS_TLS_CA_FILE` | `tls_ca_file` | `str` | `` (пусто) | Корневой сертификат для TLS-подключения. |

Заданный путь проверяется при чтении конфига (файла нет — `ConfigError`).
Простые креды (`user:password`, токен) живут в `NATS_URL`; в логи и в тексты
ошибок userinfo попадает как `nats://xxxxx@host:4222`. Для `.creds`/nkey нужен
пакет `nkeys` — его тянет `nats-py[nkeys]`, в базовых зависимостях его нет.

## Backpressure (`Config.backpressure`)

| Env | Поле | Тип | Default | Назначение |
|---|---|---|---|---|
| `NATS_MAX_IN_FLIGHT` | `backpressure.max_in_flight` | `int` | `100` | Потолок одновременно обрабатываемых Core-сообщений (вес семафора `NatsBus`). `0` → `100`, отрицательное — `ConfigError`. |

## JetStream (`Config.jetstream`)

| Env | Поле | Тип | Default | Назначение |
|---|---|---|---|---|
| `NATS_JETSTREAM_ENABLED` | `jetstream.enabled` | `bool` | `false` | Включает `StreamBus`. При `false` — `DisabledStreamBus` (все методы → `JetStreamDisabledError`). |
| `NATS_JS_STREAM_NAMES` | `jetstream.stream_names` | `tuple[str, ...]` (через запятую) | `` (пусто) | Стримы кита. Subjects стрима — `<name>.>`. Пусто — кит стримы не декларирует и не проверяет. Имя с точкой, пробелом или wildcard — `ConfigError`. |
| `NATS_JS_STREAM_MANAGED` | `jetstream.stream_managed` | `bool` | `true` | `true` — кит создаёт отсутствующие стримы и доливает свой subject в существующие. `false` — только проверяет наличие каждого стрима (отсутствие — ошибка старта). |
| `NATS_JS_RETENTION` | `jetstream.retention` | `str` | `limits` | Политика удержания создаваемого стрима: `limits` / `interest` / `workqueue` (он же `work_queue`). Иное — `ConfigError`. |
| `NATS_JS_MAX_AGE` | `jetstream.max_age` | `float, секунды` | `604800` (7 дней) | Максимальный возраст сообщения в создаваемом стриме. `0` → дефолт, отрицательное — `ConfigError`. |
| `NATS_JS_DUPLICATES` | `jetstream.duplicates` | `float, секунды` | `120` | Окно дедупа по `Nats-Msg-Id` создаваемого стрима. `0` → дефолт. Поджимается до `max_age`, если задано больше (требование JetStream). |
| `NATS_JS_MAX_IN_FLIGHT` | `jetstream.max_in_flight` | `int` | `100` | Потолок одновременно обрабатываемых stream-сообщений (семафор `JsBus`) и `max_ack_pending` консьюмера. `0` → `100`. |
| `NATS_JS_ACK_WAIT` | `jetstream.ack_wait` | `float, секунды` | `30` | Сколько сервер ждёт `ack`, прежде чем переотправить сообщение. Пока обработчик работает, кит каждые `AckWait/2` шлёт `in_progress`. `0` → `30`; значение меньше `1` — `ConfigError` (за такое окно keepalive не успевает). |
| `NATS_JS_HANDLER_TIMEOUT` | `jetstream.handler_timeout` | `float, секунды` | `0` | Бюджет одной попытки обработчика. `0` — равен `AckWait`, отрицательное — `ConfigError`. По истечении таска обработчика отменяется → `nak` → ретрай. |
| `NATS_JS_PUBLISH_ASYNC_TIMEOUT` | `jetstream.publish_async_timeout` | `float, секунды` | `5` | Сколько публикация ждёт `PubAck`. Потерянный ack завершается ошибкой в `results[i]`, а не висит вечно. `0` → `5`. |
| `NATS_JS_MAX_DELIVER` | `jetstream.max_deliver` | `int` | `10` | Потолок числа доставок одного сообщения. `0` — без лимита (бесконечный ределивери), отрицательное — `ConfigError`. `> 0` — на исчерпании DLQ/`term`. |
| `NATS_JS_NAK_BACKOFF` | `jetstream.nak_backoff` | `float, секунды` | `1` | Задержка перед повторной доставкой при NAK. `0` — мгновенный `nak` (горячая петля на устойчиво падающем сообщении). |
| `NATS_JS_DLQ_SUBJECT` | `jetstream.dlq_subject` | `str` | `` (пусто) | Subject для dead-letter. Пусто — на терминальном отказе только `term` (сообщение логируется). Значение с пробелом, пустым токеном или wildcard — `ConfigError`. |

## Декларация стримов

При `stream_managed=true` кит на старте проходит по `stream_names`:

- стрима нет — создаёт его целиком: subjects `<name>.>`, file-storage,
  `retention`/`max_age`/`duplicates` из конфига;
- стрим есть — сверяет только subjects: если `<name>.>` среди них нет, доливает
  его (объединение с существующими). `num_replicas`, `max_bytes`, `max_age`,
  `duplicates`, чужие subjects и прочие настройки оператора остаются как есть, а
  расхождение с ENV пишется в лог (`natskit: stream config drift`).

При `stream_managed=false` кит не пишет в конфигурацию стримов вообще, но
требует, чтобы каждый стрим существовал: так сервис падает на старте, а не на
первой публикации. Режим для стримов, которые ведёт платформа или соседняя
команда.

## Тюнинг poison-обработки

Три параметра определяют судьбу неуспешного stream-сообщения:

| Цель | `NATS_JS_MAX_DELIVER` | `NATS_JS_NAK_BACKOFF` | `NATS_JS_DLQ_SUBJECT` |
|---|---|---|---|
| Ограниченный ретрай + dead-letter на разбор (дефолт кита + subject) | `10` | `1` | напр. `dlq.orders` |
| Ограниченный ретрай, разбор по логам | `> 0` | `> 0` | — |
| Бесконечный ретрай (доставка не теряется, риск «застрять») | `0` | `> 0` | — |

Сообщение больше не ретраится в двух случаях: обработчик поднял
`natskit.TerminalError` (в т.ч. ошибку декода JSON у `subscribe_*_json` — битый
payload не станет валидным при ределивери) либо `deliveries >= MaxDeliver`. Тогда
кит публикует копию в `dlq_subject` (если он задан), пишет запись
`natskit: jetstream message dead` (subject, число доставок, причина) и делает
`term`. До этого — `nak` с задержкой `NakBackoff`.

Заголовки DLQ-копии:

| Заголовок | Значение |
|---|---|
| `Natskit-Dlq-Origin` | Исходный subject сообщения. |
| `Natskit-Dlq-Error` | Текст ошибки последней попытки (без переводов строк, до 200 символов). |
| `Natskit-Dlq-Reason` | `terminal` (`TerminalError`) или `max-deliver`. |
| `Natskit-Dlq-Stream` / `Natskit-Dlq-Consumer` | Стрим и durable-консьюмер, где сообщение умерло. |
| `Natskit-Dlq-Deliveries` | Сколько раз оно доставлялось. |
| `Natskit-Dlq-Instance` | Имя инстанса шины. |
| `Natskit-Dlq-Msg-Id` | Исходный `Nats-Msg-Id`, если был. |

Остальные заголовки оригинала копируются, кроме `Nats-Msg-Id` (переезжает в
`Natskit-Dlq-Msg-Id`, иначе стрим с окном дедупа счёл бы копию дублем) и
`Nats-Expected-*` (они адресованы исходному стриму и отвергли бы копию).

DLQ-subject может лежать внутри стрима: кит определяет это на старте и публикует
копию через JetStream, получая `PubAck`; иначе — Core-публикацией с `flush` (без
него отказ соединения остался бы незамеченным).

**`term` делается только после успешной DLQ-копии.** Провал публикации — это
запись `natskit: jetstream dlq publish failed, message kept for redelivery`,
`nak` и метрика со `status=dlq_error`: оригинал остаётся в стриме и уходит на
ределивери, иначе недоступный DLQ стирал бы сообщение и из стрима, и из DLQ.
Серия `nats_messages_consumed_total{status="dlq_error"}` — повод для алерта.

**Фильтр консьюмера не должен накрывать DLQ-subject.** `subscribe_stream` с
таким фильтром (`ORDERS.>` при `NATS_JS_DLQ_SUBJECT=ORDERS.dlq`) поднимает
`ConfigError`: DLQ-копия пришла бы тому же обработчику, снова упала и породила
следующую копию. Читает DLQ отдельный консьюмер — со своим конфигом, в котором
`NATS_JS_DLQ_SUBJECT` не задан.

Ограничение JetStream: DLQ-копия появляется, только если **последняя** доставка
провалилась в живом процессе. Если процесс умер и `AckWait` истёк на
`MaxDeliver`-й доставке, сервер больше не доставляет сообщение — оно остаётся в
стриме неподтверждённым до `MaxAge`.

## AckWait и HandlerTimeout

`AckWait` — таймер сервера: не получив `ack` за это время, он переотправляет
сообщение. Пока обработчик работает, кит каждые `AckWait/2` шлёт `in_progress`,
и таймер сдвигается — живой, но медленный обработчик не получает конкурентный
ределивери. Таймер срабатывает по назначению, когда процесс умер или отрезан от
сети: сообщение уходит другому инстансу не позже чем через `AckWait`.

`NATS_JS_HANDLER_TIMEOUT` — бюджет одной попытки: по истечении таска обработчика
отменяется (`CancelledError` внутри), кит делает `nak`, и сообщение идёт на
ретрай (или в DLQ по `MaxDeliver`). Обработчик, который глушит `CancelledError`,
после дедлайна теряет keepalive и получит ределивери, пока висит.

Практика: `AckWait` = за сколько сообщение должно уйти другому инстансу при
смерти процесса (десятки секунд), `HandlerTimeout` = максимальная честная
длительность обработки (минуты для тяжёлых задач). При `HandlerTimeout=0` оба
равны `AckWait`.

## Backpressure-тюнинг

`MaxInFlight` — компромисс между пропускной способностью и потреблением памяти:
выше → больше параллельных обработчиков (выше throughput при I/O-bound
обработке), но больше одновременно живущих тасок и незакрытых ресурсов. Для
JetStream это же значение становится `max_ack_pending` консьюмера — потолком
числа неподтверждённых сообщений.

При исчерпании `MaxInFlight` delivery-цикл подписки блокируется: для Core
сообщения копятся в буфере клиента, и его переполнение (`slow consumer`) — это
потеря сообщений, о которой сообщает запись `natskit: async error`; JetStream
дальше `max_ack_pending` не отправляет.

## Исчерпание reconnect'ов

При `NATS_MAX_RECONNECTS >= 0` клиент после `MaxReconnects × ReconnectWait`
закрывает соединение окончательно: подписки мертвы, `publish` возвращает ошибку,
переподключения не будет. Кит пишет об этом запись
`natskit: connection closed, reconnects exhausted`, а `Conn.healthy()` и
health-проба отдают `natskit.ConnectionClosedError` — состояние отличимо от
временного `reconnecting`. Дефолтные `-1` (бесконечные попытки) оставляют шину
живой и после длительной недоступности сервера.

## Graceful shutdown

`Conn.close(timeout=…)` в пределах `min(NATS_DRAIN_TIMEOUT, timeout)`: дренит
подписки (снимает интерес, дообрабатывает уже полученные сообщения), ждёт
завершения обработчиков, дренит публикации и закрывает соединение. Обработчики
успевают `ack`/`nak` по живому соединению. По истечении бюджета пишется
предупреждение (`natskit: drain timeout, handlers still in flight`), таски
обработчиков отменяются и соединение закрывается принудительно; незавершённые
JetStream-сообщения сервер переотправит после `AckWait`.

`NatsProvider` вызывает `close()` при закрытии контейнера, поэтому shutdown
сервиса укладывается в тот же бюджет.

## Несколько инстансов

`new_config_prefixed(name)` читает те же переменные под префиксом имени в верхнем
регистре: `<NAME>_NATS_URL`, `<NAME>_NATS_JETSTREAM_ENABLED`,
`<NAME>_NATS_JS_STREAM_NAMES` и т.д. (дефис, точка и пробел в имени становятся
`_`). Пример: `new_config_prefixed("hub")` → `HUB_NATS_*`.

Наборы инстансов изолированы: незаданная префиксная переменная берёт значение из
колонки Default, а **не** из одноимённой голой `NATS_*`. Детерминированный конфиг
без чтения ENV вообще (тесты, фейки) даёт `natskit.config_without_env(**values)`. Инстанс, который только
публикует в стрим соседней команды, поэтому оставляет
`<NAME>_NATS_JS_STREAM_NAMES` незаданным — и не пытается создать у соседей свои
стримы. Проводка двух шин — [usage.md](usage.md).
