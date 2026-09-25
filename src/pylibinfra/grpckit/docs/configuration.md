# Конфигурация grpckit

## Config (ENV)

`grpckit.Config` — публичный env-контракт сервера. Читается `new_config()` либо
через `config.load(Config, *sources)`.

> **Public API.** Имена полей и их `validation_alias` — часть стабильного
> контракта (корневой README § Стабильность API). Менять их = breaking change.

Окружение читается только по алиасу (`configkit.KitSettings`): переменная с
именем поля (`addr`) конфиг не заполняет. Пустая переменная равносильна
незаданной — берётся дефолт.

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `GRPC_ADDR` | `addr` | `str` | `:9090` | Bind-адрес HTTP/2. Голое `:port` = все интерфейсы. `:0` — порт выбирает ОС (`Server.addr()` покажет фактический). Пустая строка (только из кода) выключает TCP |
| `GRPC_UDS_PATH` | `uds_path` | `str` | `""` | Дополнительный unix-сокет для co-located sidecar'ов. Пусто — UDS выключен |
| `GRPC_MAX_RECV_MSG_SIZE` | `max_recv_msg_size` | `int`, байты | `4194304` (4 MiB) | Лимит входящего сообщения. `0` — дефолт, отрицательное — `ConfigError` |
| `GRPC_MAX_SEND_MSG_SIZE` | `max_send_msg_size` | `int`, байты | `4194304` (4 MiB) | Лимит исходящего сообщения. `0` — дефолт, отрицательное — `ConfigError` |
| `GRPC_MAX_CONCURRENT_STREAMS` | `max_concurrent_streams` | `int` | `0` | Лимит одновременных стримов на соединение. `0` — без лимита |
| `GRPC_REFLECTION` | `reflection` | `bool` | `false` | Server reflection. Требует пакета `grpcio-reflection` (в зависимостях pylibinfra его нет) |
| `GRPC_SHUTDOWN_GRACE` | `shutdown_grace` | `float`, секунды | `10` | Окно graceful-остановки; по истечении соединения рвутся. `>= 0` |
| `GRPC_HEALTH_INTERVAL` | `health_interval` | `float`, секунды | `10` | Период переоценки health-проб. `0` — переоценка выключена |
| `GRPC_HEALTH_PROBE_TIMEOUT` | `health_probe_timeout` | `float`, секунды | `5` | Дедлайн одной пробы. Не больше `GRPC_HEALTH_INTERVAL` (иначе `ConfigError`) |
| `GRPC_TLS_CERT_FILE` | `tls_cert_file` | `str` | `""` | PEM-сертификат сервера. TLS включается только вместе с ключом |
| `GRPC_TLS_KEY_FILE` | `tls_key_file` | `str` | `""` | PEM-ключ сервера. Без пары сервер слушает plaintext |
| `GRPC_KEEPALIVE_TIME` | `keepalive_time` | `float`, секунды | `7200` | Интервал серверных keepalive-ping'ов при простое. `0` — дефолт |
| `GRPC_KEEPALIVE_TIMEOUT` | `keepalive_timeout` | `float`, секунды | `20` | Ожидание ответа на ping перед разрывом. `0` — дефолт |
| `GRPC_KEEPALIVE_MIN_TIME` | `keepalive_min_time` | `float`, секунды | `20` | Минимальный интервал клиентских ping'ов, который сервер терпит при активных стримах (`grpc.http2.min_ping_interval_without_data_ms`). `0` — дефолт |
| `GRPC_KEEPALIVE_PERMIT_WITHOUT_STREAM` | `keepalive_permit_without_stream` | `bool` | `true` | Слать keepalive-ping'и и на простаивающих соединениях (`grpc.keepalive_permit_without_calls`) — так же, как это делает grpc-go-сервер |

## Правила валидации

`new_config()` и прямой `Config(...)` валидируют конфиг; `new_config()` поднимает
`grpckit.ConfigError`, прямой конструктор — pydantic `ValidationError` (тоже
`ValueError`).

| Правило | Почему |
|---|---|
| Лимиты, грейс, интервалы и keepalive `>= 0` | Отрицательный лимит отбивал бы любое сообщение, отрицательный грейс означал бы мгновенный форс-стоп |
| `GRPC_TLS_CERT_FILE` и `GRPC_TLS_KEY_FILE` — оба или ни одного | Одна половина пары означала бы «TLS задумывался, но сервер слушает plaintext» |
| `GRPC_HEALTH_PROBE_TIMEOUT <= GRPC_HEALTH_INTERVAL` (при `INTERVAL > 0`) | Пробы гоняются параллельно, поэтому цикл укладывается в период ровно тогда, когда в него укладывается самая долгая проба |
| `GRPC_ADDR` и `GRPC_UDS_PATH` не пусты одновременно | Иначе серверу некуда биндиться |

Нулевое поле — не ошибка: лимиты сообщений и keepalive заменяются дефолтом,
`GRPC_HEALTH_INTERVAL=0` выключает переоценку, `GRPC_MAX_CONCURRENT_STREAMS=0`
означает «без лимита». `Config()` без переменных даёт рабочий сервер на `:9090`.

## Совместимость keepalive с клиентом

Серверный `GRPC_KEEPALIVE_MIN_TIME` (`20s`) не строже клиентского интервала
keepalive golibinfra-клиента (`30s`) — связка «из коробки» не получает GOAWAY
`too_many_pings` на idle-стримах. Ужесточая `GRPC_KEEPALIVE_MIN_TIME`, поднимайте
и клиентский интервал: у слишком частых ping'ов есть всего три страйка, после чего
сервер закрывает соединение вместе со всеми стримами.

`GRPC_KEEPALIVE_PERMIT_WITHOUT_STREAM` управляет **отправкой** ping'ов, а не их
приёмом: grpc-core без него молчит на соединении без активных вызовов, а
grpc-go-сервер такое соединение пингует — дефолт `true` держит поведение
одинаковым. Аналога go-шного `EnforcementPolicy.PermitWithoutStream` (терпимость
к клиентским ping'ам на простаивающем соединении) в grpc-core нет: там это
встроенная политика транспорта, аргументом не настраивается.

## TLS

TLS навешивается, когда заданы **оба** `GRPC_TLS_CERT_FILE` и
`GRPC_TLS_KEY_FILE`; нечитаемый файл — `ConfigError: grpckit: tls creds: …` при
`listen()`. Когда TLS терминирует ingress/прокси перед сервисом, сам сервис
слушает plaintext — это дефолтный режим. mTLS и нестандартные параметры — через
`server_options=(("grpc.ssl_client_certificate_request_type", ...),)` или свой
`grpc.aio.server` поверх `Server.grpc()`.

## Reflection

Server reflection регистрируется только при `GRPC_REFLECTION=true` и требует
пакета `grpcio-reflection`, которого в зависимостях pylibinfra нет: без него
конструирование сервера падает `ConfigError` с явным текстом. Установите пакет в
сервисе, если reflection нужен, и передайте имена сервисов — grpc-python, в
отличие от Go, не собирает их с сервера сам. Без DI это аргумент
`Server(..., service_names=("greeter.Greeter",))`, в контейнере — кортеж
`grpckit.ServiceNames` (см. [usage.md](usage.md)); к ним кит сам добавляет health
и сам reflection. Reflection exempt от auth и раскрывает список методов, поэтому
в prod держим его выключенным.

## UDS

`GRPC_UDS_PATH` — расширение поверх golibinfra: сервер дополнительно слушает
unix-сокет (co-located sidecar ходит без сетевого хопа). Старый файл сокета
снимается перед bind, а после остановки снимается **только тот сокет, который
сервер занял сам** — остановка сервера, не дошедшего до `listen()`, не трогает
файл живого соседа по тому же пути. TCP при этом продолжает работать: чтобы
остаться только на UDS, конфиг с пустым `addr` собирается в коде
(`Config.model_validate({"addr": "", "uds_path": ...})`) — пустая переменная
окружения читается как незаданная и дала бы дефолт `:9090`.
