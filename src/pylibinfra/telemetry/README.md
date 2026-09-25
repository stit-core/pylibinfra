# telemetry

OTel-bootstrap для сервисов stit: инициализация SDK (tracer / meter / logger
providers), OTLP/HTTP-экспорт трёх сигналов в коллектор, resource-атрибуты,
W3C-propagator и system-метрики. Корневой obs-кит — без него инструментация
остальных китов инертна (no-op).

## Назначение

- **Init трёх сигналов** — `TracerProvider`, `MeterProvider`, `LoggerProvider`
  выставляются глобальными (`trace.set_tracer_provider` /
  `metrics.set_meter_provider` / `_logs.set_logger_provider`); OTel-`LoggerProvider`
  дополнительно отдаётся в dishka-граф — `LogkitProvider` подхватывает его как
  второй sink.
- **OTLP/HTTP по спецификации** — `OTEL_EXPORTER_OTLP_ENDPOINT` — базовый URL
  коллектора, который выбирает потребитель: схема задаёт TLS, per-signal пути
  `/v1/traces`, `/v1/metrics`, `/v1/logs` дописываются к нему. Аутентификация у
  коллектора — `Authorization: Bearer <OTEL_TOKEN>` и/или заголовки из
  `OTEL_EXPORTER_OTLP_HEADERS`.
- **Метрики и трейсы — только OTLP.** В stdout они не пишутся; без живого
  коллектора экспорт дропается, а ошибка экспорта уходит в лог (§ Ошибки
  экспорта). stdout-копию логов даёт `logkit`.
- **Resource-атрибуты** — `service.name`, `service.version`,
  `deployment.environment.name` из `Config`; `tenant` и `service.namespace` —
  только при непустом `OTEL_TENANT`.
- **System-метрики** — CPU/память/GC/потоки процесса и хоста через
  `SystemMetricsInstrumentor`.
- **TextMap-propagator** — `TraceContext` + `Baggage`, в том числе при
  `OTEL_SDK_DISABLED=true`: сквозной trace-контекст между сервисами не зависит от
  экспорта.

## Импорт

```python
from pylibinfra import telemetry
```

Версионирование и установка — в [корневом README](../../../README.md)
(§ Версионирование, § Стабильность API).

## Быстрый старт

`TelemetryProvider` требует в графе `telemetry.Config` и `logkit.Config` (второй
— чтобы построить логгер ошибок экспорта). Конфиги регистрирует консьюмер;
`logkit.RedactConfig` — опционально.

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import logkit, telemetry


class AppConfigs(Provider):
    scope = Scope.APP

    @provide
    def telemetry_config(self) -> telemetry.Config:
        return telemetry.new_config()  # OTEL_SERVICE_NAME обязателен, если не disabled

    @provide
    def log(self) -> logkit.Config:
        return logkit.new_config()


container = make_async_container(
    logkit.LogkitProvider(),
    telemetry.TelemetryProvider(),  # регистрируется ПОСЛЕ logkit: перекрывает его OTel-sink
    AppConfigs(),
)
```

В dishka ресурс не создаётся, пока его не запросят: **`Providers` резолвится на
старте приложения, первым** — иначе глобальные провайдеры не выставлены, киты
пишут в no-op, а `shutdown` не вешается на закрытие контейнера.

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await telemetry.start(app.state.dishka_container)  # SDK поднят, shutdown — на close()
    yield
```

Инструменты киты и приложение берут у глобальных провайдеров
(`trace.get_tracer(...)` / `metrics.get_meter(...)`), а бизнес-метрики — через
[`bizmetric`](../bizmetric/). `TelemetryProvider` эти интерфейсы в граф не
кладёт: проводка потребителя остаётся за потребителем.

### Без DI

```python
providers = telemetry.init(telemetry.new_config(), error_logger=log)
...
providers.shutdown(timeout=30.0)
```

## Конфигурация

`telemetry.Config` — **part of public API**; `new_config()` валидирует её
(`telemetry.ConfigError`).

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `OTEL_SDK_DISABLED` | `disabled` | `bool` | `false` | `true` → провайдеры без экспортёров: сигналы дропаются, сети нет; propagator и глобальные провайдеры выставляются |
| `OTEL_SERVICE_NAME` | `service_name` | `str` | — (**required**, если не `OTEL_SDK_DISABLED`) | `service.name` |
| `OTEL_SERVICE_VERSION` | `service_version` | `str` | `dev` | `service.version` — semver / git short-sha |
| `APP_ENV` | `environment` | `str` | `dev` | `deployment.environment.name` — `prod` / `staging` / `smoke` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `otlp_endpoint` | `str` | `http://localhost:4318` | базовый URL коллектора: `http://` — без TLS, `https://` — TLS; путь сохраняется, per-signal `/v1/<signal>` дописывается. `host:port` без схемы → `ConfigError` |
| `OTEL_TRACES_SAMPLER_ARG` | `sample_ratio` | `float` | `1.0` | доля сэмплируемых трейсов, `[0, 1]` (`ParentBased(TraceIdRatioBased)`) |
| `OTEL_EXPORT_TIMEOUT` | `export_timeout` | `float`, секунды | `10.0` | таймаут одного OTLP-запроса у всех трёх экспортёров и их процессоров; `> 0` |
| `OTEL_TRACES_BATCH_DELAY` | `trace_batch_delay` | `float`, секунды | `5.0` | период сброса очереди batch-процессора трейсов; `> 0` |
| `OTEL_METRICS_INTERVAL` | `metric_interval` | `float`, секунды | `10.0` | период экспорта метрик; `> 0` |
| `OTEL_LOGS_BATCH_DELAY` | `log_batch_delay` | `float`, секунды | `1.0` | период сброса очереди batch-процессора логов; `> 0` |
| `OTEL_TENANT` | `tenant` | `str` | `""` | непустой выставляет resource-атрибуты `service.namespace` и `tenant` (одно значение); пусто — их нет |
| `OTEL_EXPORTER_OTLP_HEADERS` | `otlp_headers` | `dict[str, str]` | `{}` | заголовки всех трёх экспортёров, формат спецификации `k1=v1,k2=v2` (значения percent-encoded). Имена канонизируются (`authorization` → `Authorization`); невалидное имя → `ConfigError`. В `repr` конфига и текстах ошибок значения не показываются — сломанная запись называется номером |
| `OTEL_TOKEN` | `otlp_bearer_token` | `SecretStr` | `""` | `Authorization: Bearer <token>` к каждому OTLP-запросу; перекрывает `Authorization` из `OTEL_EXPORTER_OTLP_HEADERS`. В `repr` конфига и текстах ошибок не показывается |

`new_config()` отвергает: пустой `service_name` при включённой телеметрии,
`sample_ratio` вне `[0, 1]`, неположительные таймеры, endpoint без схемы или
хоста, невалидное имя заголовка.

### Spec-переменные OTel: что действует

Часть значений кит задаёт явными опциями SDK — они перекрывают соответствующие
переменные спецификации. Остальные доходят до SDK как есть.

| Переменная | Действует | Почему |
|---|---|---|
| `OTEL_RESOURCE_ATTRIBUTES` | да | читает `Resource.create()`; одноимённые атрибуты кита (`service.*`, `deployment.environment.name`) перекрывают её значения |
| `OTEL_EXPORTER_OTLP_COMPRESSION` | да | кит компрессию не задаёт |
| `OTEL_EXPORTER_OTLP_CERTIFICATE` | да | кит TLS-конфиг не задаёт (схема URL задаёт только insecure/secure) |
| `OTEL_BSP_MAX_QUEUE_SIZE`, `OTEL_BSP_MAX_EXPORT_BATCH_SIZE` | да | кит задаёт только задержку и таймаут batch-процессора трейсов |
| `OTEL_BLRP_MAX_QUEUE_SIZE`, `OTEL_BLRP_MAX_EXPORT_BATCH_SIZE` | да | то же для процессора логов |
| `OTEL_SDK_DISABLED` | да | читают и кит, и SDK: провайдеры отдают no-op инструменты |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` и per-signal аналоги | нет | endpoint собирается китом из `OTEL_EXPORTER_OTLP_ENDPOINT` |
| `OTEL_EXPORTER_OTLP_TIMEOUT` | нет | перекрыт `OTEL_EXPORT_TIMEOUT` |
| `OTEL_BSP_SCHEDULE_DELAY`, `OTEL_BLRP_SCHEDULE_DELAY` | нет | перекрыты `OTEL_TRACES_BATCH_DELAY` / `OTEL_LOGS_BATCH_DELAY` |
| `OTEL_BSP_EXPORT_TIMEOUT`, `OTEL_BLRP_EXPORT_TIMEOUT` | нет | перекрыты `OTEL_EXPORT_TIMEOUT` |
| `OTEL_METRIC_EXPORT_INTERVAL`, `OTEL_METRIC_EXPORT_TIMEOUT` | нет | перекрыты `OTEL_METRICS_INTERVAL` / `OTEL_EXPORT_TIMEOUT` |
| `OTEL_TRACES_SAMPLER` | нет | sampler фиксирован: `ParentBased(TraceIdRatioBased(OTEL_TRACES_SAMPLER_ARG))`. `OTEL_TRACES_SAMPLER_ARG` действует всегда, а не только при `traceidratio` |
| `OTEL_EXPORTER_OTLP_HEADERS` (в экспортёре) | нет | кит разбирает переменную сам и передаёт экспортёрам готовый набор вместе с `OTEL_TOKEN` |

## Observability

### Что инициализирует

- **TracerProvider** — `ParentBased(TraceIdRatioBased(sample_ratio))`,
  batch-процессор (`trace_batch_delay`, `export_timeout`), экспорт на
  `<endpoint>/v1/traces`.
- **MeterProvider** — periodic-reader (`metric_interval`, `export_timeout`),
  экспорт на `<endpoint>/v1/metrics`.
- **LoggerProvider** — batch-процессор (`log_batch_delay`, `export_timeout`),
  экспорт на `<endpoint>/v1/logs`; выставляется глобальным и отдаётся в граф как
  OTel-`LoggerProvider` и как `logkit.OptionalLoggerProvider`.
- **Composite-propagator** — `TraceContext` + `Baggage` (и при `OTEL_SDK_DISABLED`).

### Что эмитит

Своих метрик у кита нет. Единственный источник сигналов — `SystemMetricsInstrumentor`
(scope `opentelemetry.instrumentation.system_metrics`, данные из `psutil`):

| Группа | Метрики | Атрибуты |
|---|---|---|
| CPU хоста | `system.cpu.time`, `system.cpu.utilization` | `cpu`, `state` ∈ `idle`/`user`/`system`/`irq` |
| Память хоста | `system.memory.usage`, `system.memory.utilization`, `system.swap.usage`, `system.swap.utilization` | `state` ∈ `used`/`free`/`cached` |
| Диск хоста | `system.disk.io`, `system.disk.operations`, `system.disk.time` | `device`, `direction` ∈ `read`/`write` |
| Сеть хоста | `system.network.io`, `system.network.packets`, `system.network.errors`, `system.network.dropped_packets` | `device`, `direction` |
| Соединения хоста | `system.network.connections` | `family`, `protocol`, `state`, `type` |
| Потоки хоста | `system.thread_count` | — |
| Процесс | `process.cpu.time`, `process.cpu.utilization`, `process.memory.usage`, `process.memory.virtual`, `process.thread.count`, `process.open_file_descriptor.count`, `process.context_switches`, `process.disk.io` | `type` ∈ `user`/`system`/`voluntary`/`involuntary`, `direction` |
| Runtime CPython | `process.runtime.cpython.memory`, `…cpu_time`, `…cpu.utilization`, `…thread_count`, `…context_switches`, `…gc_count` | `type` ∈ `rss`/`vms`, `count` |
| GC CPython | `cpython.gc.collections`, `cpython.gc.collected_objects`, `cpython.gc.uncollectable_objects` | `generation` |

`system.*` описывают **хост**, а не контейнер: на общей ноде их шлёт каждый под.
Для per-pod-картины ориентируйтесь на `process.*` / `process.runtime.cpython.*` /
`cpython.gc.*` — они соответствуют runtime-метрикам Go-сервисов. Набор метрик
доступен ровно настолько, насколько его отдаёт `psutil` на конкретной ОС.

### Ошибки экспорта

`init(cfg, error_logger=...)` заворачивает диагностику SDK в `logkit`-логгер:
экспортёры и внутренние компоненты SDK пишут в stdlib-логгеры пространства
`opentelemetry`, кит перехватывает их своим handler'ом.

| Канал | Что несёт | Уровень записи |
|---|---|---|
| `logging.ERROR` | ошибки экспорта (отказ коллектора, таймаут, исчерпанные ретраи) | `error` |
| `logging.WARNING` | «dropped log records», переполнение очереди, обрезка атрибутов | `warn` |
| `logging.INFO` / `logging.DEBUG` | внутренние сообщения SDK | `debug` |

Повторяющийся текст логируется не чаще раза в минуту, чтобы недоступный
коллектор не заливал лог на каждом батче; число отслеживаемых текстов ограничено
256. `propagate` у логгера `opentelemetry` снимается: запись не должна уйти во
второй раз через root-handler `logkit`, у которого есть OTel-sink.

`TelemetryProvider` строит логгер ошибок из `logkit.Config` и пишет **только в
локальный sink, без OTel-sink'а**: иначе ошибка экспорта логов породила бы новую
запись, новый экспорт и новую ошибку. `logkit.RedactConfig` из графа при этом
применяется — в текст ошибки экспорта попадают URL коллектора и атрибуты
сообщений SDK. Без `error_logger` диагностика остаётся в stdlib-`logging`, как её
пишет SDK.

### Resource-атрибуты — кто что ставит

| Attr | Источник | Кто ставит |
|---|---|---|
| `service.name` | `OTEL_SERVICE_NAME` | **приложение** |
| `service.version` | `OTEL_SERVICE_VERSION` | **приложение** |
| `deployment.environment.name` | `APP_ENV` | **приложение** |
| `tenant` | `OTEL_TENANT` | **приложение**, опционально |
| `service.namespace` | `OTEL_TENANT` (то же значение) | **приложение**, опционально |

Пустой `OTEL_TENANT` не выставляет ни одного из двух атрибутов. Если их
проставляет сам коллектор, оставьте переменную пустой — у атрибута должен быть
один источник.

## API

| Символ | Назначение |
|---|---|
| `Config` | конфиг bootstrap'а; `ConfigError` при невалидных значениях |
| `Config.headers() -> dict[str, str]` | заголовки OTLP-запросов: `OTEL_EXPORTER_OTLP_HEADERS` + Bearer из `OTEL_TOKEN` |
| `new_config() -> Config` | ENV + валидация |
| `signal_url(endpoint, signal) -> str` | `<endpoint>/v1/<signal>` по спецификации; невалидный endpoint — `ValueError` |
| `init(cfg, *, error_logger=None) -> Providers` | bootstrap без DI: провайдеры, глобальные провайдеры, propagator, system-метрики |
| `Providers` | `tracer_provider` / `meter_provider` / `logger_provider` (SDK-типы) + `export_timeout` |
| `Providers.shutdown(timeout=None)` | гасит tracer → meter → logger, деля `timeout` между шагами; сбои собираются в `ExceptionGroup`, остановка не прерывается |
| `TelemetryProvider` | dishka-Provider: `Providers` (+ `shutdown` на закрытии контейнера), OTel-`LoggerProvider`, `logkit.OptionalLoggerProvider`. Принимает `Config`, `logkit.Config` и опц. `logkit.RedactConfig`. **Config не provide'ит**, `Tracer`/`Meter` — тоже |
| `start(container) -> Providers` | резолвит `Providers` на старте приложения |
| `ConfigError` | невалидный конфиг телеметрии |

## Тестирование

Метрики и трейсы всегда идут в OTLP — чтобы посмотреть их локально, поднимите
коллектор и нацельте `OTEL_EXPORTER_OTLP_ENDPOINT`. Логи видны в консоли через
`logkit` без коллектора.

Юнит-тесты пакета: сборка per-signal URL, валидация конфига, разбор и мерж
заголовков; resource-атрибуты (с `OTEL_TENANT` и без); `init` при
`OTEL_SDK_DISABLED` (propagator, глобальные провайдеры, спаны без записи);
`init` против `http.server`-коллектора — все три сигнала приходят на
`<base>/v1/{traces,metrics,logs}` с Bearer- и env-заголовками; контейнер dishka
(shutdown на закрытии сливает очередь логов, redact доходит до логгера ошибок,
граф не занимает `Meter`/`Tracer`); деление дедлайна `shutdown` между сигналами и
откат уже поднятых провайдеров при сбое `init`; маршрутизация, уровни и
дросселирование диагностики SDK.

```bash
uv run pytest tests/telemetry
```

## Инварианты и подводные камни

- **`OTEL_SERVICE_NAME` обязателен при включённой телеметрии** — `new_config()`
  поднимает `ConfigError`. Без корректных `service.*` /
  `deployment.environment.name` сервис не виден на дашбордах.
- **`Providers` резолвится первым, на старте.** dishka ленив: без запроса SDK не
  поднимется, глобальные провайдеры не встанут, `shutdown` не повесится —
  инструментация всех китов останется no-op. `telemetry.start(container)` — это
  и есть тот запрос.
- **`TelemetryProvider` регистрируется после `LogkitProvider`** — иначе его
  OTel-sink не перекроет дефолт `logkit` и логи не уйдут в OTLP.
- **`TelemetryProvider` не отдаёт `Tracer`/`Meter`.** Их провайдит потребитель
  (или берёт `trace.get_tracer`/`metrics.get_meter` по месту) — библиотечный кит
  не занимает в графе общие интерфейсы.
- **`OTEL_EXPORTER_OTLP_ENDPOINT` — полный URL.** Схема задаёт TLS, путь
  сохраняется (`https://otlp.example.com/otlp` → `…/otlp/v1/traces`). В тексте
  ошибки userinfo редактируется (`http://<redacted>@host:4318`).
- **Бюджет остановки делится на три сигнала.** `shutdown(timeout)` даёт каждому
  провайдеру `min(OTEL_EXPORT_TIMEOUT, остаток / число оставшихся шагов)`, но не
  меньше 0.1 s: шаг, переживший свой бюджет, не обнуляет следующие — при
  недоступном коллекторе логи (последние в порядке) всё равно получают живое
  время. Контейнер закрывается с бюджетом `3 × OTEL_EXPORT_TIMEOUT`.
- **Бюджет ограничивает сброс очереди, а не весь шаг.** OTel-python не принимает
  таймаут в `shutdown()` провайдера, поэтому уже начатый HTTP-запрос экспортёра
  досчитывается — сверх бюджета шаг может занять ещё до `OTEL_EXPORT_TIMEOUT`.
  Уменьшать нужно `OTEL_EXPORT_TIMEOUT`, а не `timeout` у `shutdown`.
- **`init` вызывается один раз за процесс.** Глобальные провайдеры OTel-python
  выставляются set-once: повторный вызов оставит первые и напишет предупреждение.
- **Сбой остановки виден на закрытии контейнера.** `shutdown` не глотает ошибки:
  `container.close()` поднимет `ExceptionGroup`. Финализатор `Providers` идёт
  последним (ресурс резолвится первым), поэтому остальные киты уже закрыты.
- **Правка `telemetry` — breaking для всех сервисов, подключивших кит.** `Config`,
  resource-атрибуты и набор сигналов — контракт, на который опираются их env,
  запросы к бэкенду наблюдаемости и дашборды.
