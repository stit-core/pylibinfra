# Observability tgbotkit

Единый источник правды по сигналам кита. Метрики и спан кит эмитит сам, записи пишет `logkit`,
готовность отдаёт `healthkit` через `/ready` сервера `httpkit`.

## Метрики

Инструменты создаются на meter'е `pylibinfra.tgbotkit` (`otelkit.meter`) в конструкторе объекта,
а не на уровне модуля; без `MeterProvider` — no-op. Тест, ставящий свой провайдер, обязан сделать
это до создания `Instruments`.

| Метрика | Тип | Labels | Что |
|---|---|---|---|
| `tg_updates_total` | counter | `type` (тип апдейта: `message`, `callback_query`, …; `unknown` — тип не определился), `result` (`ok` \| `error`) | Обработанные апдейты, включая фоновые события диалога |
| `tg_update_duration_seconds` | histogram (s, общие бакеты `0.005 … 30`) | `type`, `result` | Длительность обработки от входа в цепочку кита до выхода |
| `tg_update_panics_total` | counter | `type` | Необработанные исключения обработчика, погашенные recovery |
| `tg_updates_deduped_total` | counter | `type` | Апдейты, отброшенные как повтор уже взятого `update_id` |
| `tg_updates_throttled_total` | counter | `type` | Апдейты, отброшенные per-user лимитом `BOT_THROTTLE_RPS` |
| `tg_redis_errors_total` | counter | `op` (`dedup` \| `throttle` \| `rate`) | Операции, на которых Redis был недоступен и кит деградировал fail-open |
| `tg_payments_total` | counter | `stage` (`pre_checkout` \| `paid` \| `refunded` \| `refund`), `result` (`ok` \| `rejected` \| `error` \| `duplicate`) | Исходы платёжных операций |

`type` — конечное множество типов Bot API, поэтому годится как label; `chat_id`, `user_id` и
`update_id` в labels не попадают никогда — они уходят в поля лога. Апдейт незнакомого этой
версии aiogram типа получает `type=unknown` и `result=ok`: диспетчер отдаёт его как
необработанный, а не роняет цепочку. Отменённый на остановке апдейт считается `result=error` —
он не обработан.

`tg_redis_errors_total` — сигнал деградации, а не отказа: пока он растёт, дедуп и лимиты
выключены, и алерт на него ставится вместе с алертом на пробу `tgbotkit/redis`.

Стадии `tg_payments_total` разведены по источнику события: `pre_checkout` ставит
`answer_pre_checkout`, `refund` — `refund_charge`, а `paid`/`refunded` эмитят `ChargeGuard` и
`RefundGuard` на дубликате входящего апдейта.

## Спан

| Сигнал | Деталь |
|---|---|
| `tg.update` | Один спан на апдейт; открывает `ObservabilityMiddleware`, внутренний в цепочке кита |
| Атрибуты | `tg.update_type` |
| Ошибка | Исключение обработчика записывается в спан (`record_exception`) и ставит статус `ERROR`, после чего пробрасывается в recovery |
| Родитель | В webhook-режиме — server-спан `httpkit` того же запроса, поэтому приём и обработка сшиты в один трейс; в polling это корневой спан |

## Request-id

`RequestIdMiddleware` кладёт id в общий contextvar (`_internal.requestid`): валидный входящий id
сохраняется (в webhook-режиме его уже поставил `httpkit`), иначе заводится UUID v7. Благодаря
общему contextvar'у `logkit` пишет поле `request_id` сам, а `httpclient` продолжает ту же цепочку
в вызовах бэкенда — без явной передачи. Фоновая доставка (`Delivery`) идёт под своим свежим id:
её никто не «запрашивал», и склеивать её с чужим апдейтом было бы неверно.

## Ctx-поля логов

Кит регистрирует их через `logkit.register_attr`, поэтому они попадают в **каждую** запись,
сделанную во время обработки апдейта, включая записи кода сервиса:

| Поле | Значение |
|---|---|
| `update_id` | `update_id` апдейта; у фонового события диалога поля нет |
| `tg_user_id` | Telegram-id пользователя; вне пользовательского апдейта поля нет |
| `chat_id` | Id чата; вне чата поля нет |

## Записи кита

| Уровень | Сообщение | Когда |
|---|---|---|
| error | `tgbotkit: update handler panicked` | Recovery погасил исключение; поле `err` |
| error | `tgbotkit: error UX failed` | `ErrorUX` сам упал при показе нейтрального ответа |
| error | `tgbotkit: pre-checkout validation failed, refusing` | `decide` упал или не уложился в дедлайн; поле `err` (нужен `log=`) |
| error | `tgbotkit: shutdown step failed` | Шаг остановки бросил исключение; поле `step` (`notifications`, `isolation`, `container`, `polling`, `http`, `delete_webhook`, `emit_shutdown`) |
| error | `tgbotkit: notification drain failed` | Дренаж подписки бросил не-таймаут; поле `err` |
| error | `tgbotkit: notification unsubscribe failed` | Откат уже открытых подписок после сорванного старта; поле `err` |
| warn | `tgbotkit: dedup unavailable, update passed through` | Redis недоступен, дедуп пропущен (fail-open) |
| warn | `tgbotkit: throttle unavailable, update passed through` | Redis недоступен, лимит пропущен (fail-open) |
| warn | `tgbotkit: rate limit unavailable, sending without a slot` | Redis недоступен, исходящий ушёл без слота (fail-open) |
| warn | `tgbotkit: flood control hit, backing off` | Telegram ответил `429`; поля `retry_after`, `attempt` |
| warn | `tgbotkit: flood control retry_after over the cap, giving up` | `retry_after` больше `BOT_RATE_MAX_WAIT`; поля `retry_after`, `cap`; исключение уходит вызывающему |
| warn | `tgbotkit: rate limit wait capped, sending anyway` | Ожидание слота превысило `BOT_RATE_MAX_WAIT`; поля `key`, `wait` |
| warn | `tgbotkit: markup rejected by telegram, resending as plain text` | Фоновая доставка повторена без разметки; поля `chat_id`, `err` |
| warn | `tgbotkit: undecodable webhook body dropped` | Тело POST не разобралось в JSON; ответ всё равно `200` |
| warn | `tgbotkit: undecodable update dropped` | Тело вебхука не разобралось в `Update`; ответ всё равно `200` |
| warn | `tgbotkit: notifiers registered without a StreamBus, skipping subscriptions` | В контейнере нет `StreamBus` |
| warn | `tgbotkit: notification drain timed out` | Обработчики подписок не уложились в бюджет `BOT_SHUTDOWN_TIMEOUT` |
| warn | `tgbotkit: polling stop timed out, cancelling` | Диспетчер не остановился за `BOT_SHUTDOWN_TIMEOUT`; задача отменяется |
| info | `tgbotkit: http listening` | Порт занят; поле `addr` |
| info | `tgbotkit: notifications started` | Поля `notifiers`, `subscriptions` |

Тела апдейтов, тексты сообщений и токен бота в записи кита не попадают: из ошибки разбора
берутся только путь поля и тип ошибки (`_decode_error`), из `TelegramBadRequest` — сообщение
Telegram, из остального — `repr` исключения.

Логи aiogram и uvicorn идут через stdlib `logging`; единый формат им даёт `logkit.capture_stdlib`,
который `LogkitProvider` зовёт сам.

## Пробы

| Имя | Проверка | Ошибка |
|---|---|---|
| `tgbotkit` | `getMe` | `TgbotkitError("tgbotkit: get_me: …")` |
| `tgbotkit/redis` | `PING` | `TgbotkitError("tgbotkit: redis ping: …")` |

Обе — фабрики (`probe(bot)`, `redis_probe(client)`); в кортеж `healthkit.Probes` их складывает
консьюмер, а `/ready` сервера `httpkit` гоняет их параллельно с дедлайном
`HTTP_READY_PROBE_TIMEOUT`. Проба `tgbotkit` ходит в Telegram, поэтому её частота — это частота
readiness-проверок: недоступность Bot API делает под NotReady.

Бот с NATS добавляет к ним `natskit.probe(conn)`, бот с Postgres — `pgkit.probe(db)`: кит их не
подставляет, состав кортежа решает сервис.
