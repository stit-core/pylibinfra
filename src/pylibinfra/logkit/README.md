# logkit

Структурный `Logger`: JSON или text в локальный sink (stdout либо произвольный
writer), опциональный второй sink в OTel `LoggerProvider` (OTLP), PII-redact на
уровне полей, `request_id`/`trace_id`/`span_id` из контекста, `register_attr` и
метрика `log_messages_total{level}`.

## Назначение

`logkit` — единая точка логирования сервисов stit. Одна цепочка обработки, один
формат записи, две точки выхода:

- **Локальный sink** — то, что собирает `docker logs` / файловый сборщик:
  JSON (`LOG_FORMAT=json`) или human-readable text (`LOG_FORMAT=text`, для
  локальной разработки). При `LOG_SILENT=true` записи отбрасываются;
  произвольный writer — опция `writer=`.
- **OTel Logs (OTLP)** — второй sink, включается опцией `otel=` или через
  dishka, если в графе есть OTel `LoggerProvider` (его отдаёт
  `telemetry.TelemetryProvider`). Уровень, `msg` и поля маппятся в `LogRecord`;
  `trace_id`/`span_id` SDK берёт из активного спана сам.
- **Ctx-поля на верхнем уровне записи** — `request_id` из общего contextvar'а
  (его ставит входящий транспорт: httpkit/grpckit/tgbotkit), `trace_id`/`span_id`
  из активного спана и поля, зарегистрированные через `register_attr`.
- **PII-redact** — `redact=` + `RedactConfig`: regex-маски применяются к `msg` и
  строковым значениям полей, включая `with_`-поля, вложенные dict/list и текст
  исключений; числа, `bool` и `None` не трогаются. Работает до fan-out —
  маскируются оба sink'а.
- **Fail-fast** — невалидный `LOG_LEVEL`/`LOG_FORMAT`, regex в
  `LOG_REDACT_PATTERNS` или строка `LOG_REDACT_REPLACE_WITH` дают `ConfigError`
  при конструировании логгера, а не на первой записи.

В production логировать только через `logkit.Logger`. `print()` и
`logging.getLogger()` в прод-коде запрещены: они проходят мимо
trace-correlation, redact'а, метрики и единого формата.

## Импорт

```python
from pylibinfra import logkit
```

Версионирование и установка — в [корневом README](../../../README.md)
(§ Версионирование, § Стабильность API).

## Быстрый старт

### Через dishka

`LogkitProvider` provide'ит `logkit.Logger`, но **не** конфиги — их регистрирует
консьюмер. `RedactConfig` и OTel `LoggerProvider` — опциональные зависимости:
кит даёт дефолт `None`, а провайдер, зарегистрированный **позже**, его
перекрывает (последний в контейнере побеждает). Отсюда порядок: киты, затем
`TelemetryProvider`, затем App-Provider консьюмера с конфигами.

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import logkit, telemetry


class AppConfigs(Provider):
    scope = Scope.APP

    @provide
    def log(self) -> logkit.Config:
        return logkit.new_config()

    @provide
    def redact(self) -> logkit.OptionalRedactConfig:
        return logkit.new_redact_config()

    @provide
    def telemetry_config(self) -> telemetry.Config:
        return telemetry.new_config()


container = make_async_container(
    logkit.LogkitProvider(),  # → logkit.Logger
    telemetry.TelemetryProvider(),  # опционально: даёт OTel-sink и Providers
    AppConfigs(),  # конфиги регистрирует консьюмер
)

await telemetry.start(container)  # поднять SDK на старте
log = await container.get(logkit.Logger)
log.info("service started")
```

Граф без `TelemetryProvider` собирается и работает: OTLP-sink не подключается,
локальный sink пишет как обычно.

### Без DI

```python
cfg = logkit.new_config()  # или logkit.Config(level="debug")
log = logkit.new(cfg)  # JSON в stdout

log = logkit.new(
    cfg,
    writer=stream,  # writer вместо stdout
    redact=logkit.new_redact_config(),  # PII-маски
    otel=providers.logger_provider,  # второй sink в OTLP
    service=logkit.new_service_config(),  # service/env/version в каждой записи
)
```

Каждый вызов `new` даёт независимый логгер со своей цепочкой; общего состояния
процесса у логгеров нет.

### Логирование с полями

```python
def handle(order_id: str, log: logkit.Logger) -> None:
    log.info("processing order", order_id=order_id, amount=1500)
    # → {"time":…,"level":"INFO","msg":"processing order","order_id":…,"amount":1500,
    #    "request_id":…,"trace_id":…,"span_id":…}
```

`with_(**kv)` возвращает `Logger` с прикреплёнными полями. `enabled(level)`
позволяет пропустить дорогую подготовку полей:

```python
if log.enabled("debug"):
    log.debug("payload", body=expensive_dump(request))
```

### register_attr — проброс полей из контекста

`register_attr` регистрирует под ключом функцию, которую kit зовёт на **каждой**
записи; непустое значение попадает в оба sink'а, всегда на верхнем уровне:

```python
logkit.register_attr("tenant", lambda: current_tenant() or None)
```

Пустой ключ или не-callable — `ValueError`. Повторная регистрация того же ключа
заменяет предыдущую (соседние ключи не трогает), поэтому дважды собранный граф
(тест и `main`) не даёт дублей поля. Исключение внутри экстрактора запись не
роняет: поле пропускается, остальные поля пишутся как обычно.

### Redact

```python
log = logkit.new(cfg, redact=logkit.RedactConfig(enabled=True))
log.with_(owner="owner@example.com").info(
    "login john@example.com", phone="+79161234567", elapsed_ms=1500
)
# → "msg":"login [REDACTED]", "owner":"[REDACTED]", "phone":"[REDACTED]", "elapsed_ms":1500
```

### Записи сторонних библиотек

`capture_stdlib(log, cfg)` уводит корневой `logging` в `logkit`: uvicorn, grpc и
aiogram пишут тем же форматом и уровнем. `LogkitProvider` вызывает его сам —
это единственное изменение глобального состояния процесса, которое делает кит
(второе — реестр `register_attr`).

## Конфигурация

Конфиги читаются через `pydantic-settings`. Имена полей и env-alias'ы —
**part of public API** (корневой README § Стабильность API).

### `Config`

Конфиг по умолчанию валиден: `info`, JSON, stdout, без `source`.

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `LOG_LEVEL` | `level` | `str` | `info` | Минимальный уровень (`debug`/`info`/`warn`/`error`, регистр не важен; пустая строка = `info`). Иное значение → `ConfigError` |
| `LOG_FORMAT` | `format` | `str` | `json` | `json` — прод и сборщики логов; `text` — локальная разработка. Иное значение → `ConfigError` |
| `LOG_SOURCE` | `source` | `bool` | `false` | Добавляет `source` (файл/строка/функция вызывающего) в каждую запись локального sink'а |
| `LOG_SILENT` | `silent` | `bool` | `false` | `true` — локальный sink отбрасывает записи (OTel-sink, если подключён, работает). `writer=` приоритетнее: явный writer пишет и при `LOG_SILENT=true` |

Поле `time` — ISO-8601 с микросекундами и offset'ом локальной зоны процесса
(`2026-05-14T10:30:00.123456+03:00`); для UTC-времени запускайте процесс с
`TZ=UTC`. Поле `level` — в верхнем регистре (`INFO`), label метрики — в нижнем.

### `RedactConfig`

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `LOG_REDACT_ENABLED` | `enabled` | `bool` | `false` | Включает redact-слой |
| `LOG_REDACT_PATTERNS` | `patterns` | `tuple[str, ...]` | *(пусто → дефолтные)* | Regex-фильтры; в ENV разделитель `;` (запятая встречается в квантификаторах `{10,15}`), литеральная `;` внутри паттерна пишется как `\;`. Невалидный regex → `ConfigError`. Паттерн, совпадающий с пустой строкой (`a*`, `x?`, `secret\|`, пустой элемент), тоже → `ConfigError`: он вставляет замену между каждой парой символов записи |
| `LOG_REDACT_REPLACE_WITH` | `replace_with` | `str` | `[REDACTED]` | Строка-замена, подставляется литерально. `"`, `\` и непечатаемые символы отвергаются (`ConfigError`): они ломают JSON-представление записи |

Дефолтный набор (применяется, когда `patterns` пуст) — эвристики компактных форм
PII и очевидных секретов:

| Что | Паттерн |
|---|---|
| email | `[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}` |
| телефон | `\+\d{10,15}\b\|\b[78]\d{10}\b` |
| карта | `\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b` |
| `Authorization: Bearer` | `(?i:\bbearer\s+[A-Za-z0-9\-._~+/]+=*)` |
| `Authorization: Basic` | `(?i:\bbasic\s+[A-Za-z0-9+/]{8,}={0,2})` |
| JWT | `\beyJ[A-Za-z0-9_-]{6,}\.eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]+` |
| Anthropic API key | `\bsk-ant-[A-Za-z0-9_-]{16,}` |
| generic API key | `\bsk-[A-Za-z0-9]{20,}\b` |

Десятизначные идентификаторы без `+`/ведущей `7`/`8`, UUID, hex-хэши и короткие
base64-строки не маскируются. Свой набор задаётся целиком — дефолтные при этом
не применяются.

Дефолтный набор проходит литеральный pre-filter: строка без `@`, `+`, `bearer`,
`basic`, `eyJ`, `sk-` и без цепочки от 11 цифр до regex'ов не доходит. Поэтому
дефолтный набор компилируется в ASCII-режиме: `\d`/`\s`/`\b` в нём совпадают ровно
с тем, что знает pre-filter, — юникод-цифры и неразрывный пробел он не ловит (как
и RE2 в golibinfra). Свой набор компилируется как написан и pre-filter не
проходит: каждый его паттерн прогоняется по каждой строке записи, поэтому даже
одна узкая маска стоит дороже отфильтрованного дефолтного набора.

### `ServiceConfig` (опция `service=`)

| Env | Поле | Тип | Default | Назначение |
|---|---|---|---|---|
| `OTEL_SERVICE_NAME` | `service` | `str` | `""` | поле `service` |
| `APP_ENV` | `env` | `str` | `dev` | поле `env` |
| `OTEL_SERVICE_VERSION` | `version` | `str` | `dev` | поле `version` |

Опция `service=` добавляет непустые поля в каждую запись — для sink'а без
OTel-resource (файловый сборщик). `LogkitProvider` эту опцию **не** применяет: в
OTLP идентичность сервиса несёт resource из `telemetry`.

## Observability

| Метрика | Тип | Labels | Смысл |
|---|---|---|---|
| `log_messages_total` | counter | `level` ∈ `debug`/`info`/`warn`/`error` | Эмитированные записи: инкремент стоит после проверки уровня, поэтому отфильтрованные вызовы (`debug` при `LOG_LEVEL=info`) не считаются |

Meter — `otelkit.meter("logkit")`, instrumentation scope `pylibinfra.logkit` с
версией дистрибутива. Без сконфигурированного `MeterProvider` — no-op.

**OTel-sink** эмитит `LogRecord` с `timestamp`/`observed_timestamp`, `severity`
(Debug 5 / Info 9 / Warn 13 / Error 17), `severity_text` (`INFO`), `body = msg`,
полями записи в атрибутах и trace/span id из активного спана (их проставляет
SDK). Scope — `pylibinfra.logkit` той же версии. Уровень фильтрации — тот же
`LOG_LEVEL`. Значения `None` в атрибуты не попадают.

`trace_id`/`span_id` отдельными полями пишет только локальный sink: в OTLP они
уже поля записи. Спанов kit не эмитит.

## API

```python
class Logger(Protocol):
    def debug(self, msg: str, /, **kv: Any) -> None: ...
    def info(self, msg: str, /, **kv: Any) -> None: ...
    def warn(self, msg: str, /, **kv: Any) -> None: ...
    def error(self, msg: str, /, **kv: Any) -> None: ...
    def with_(self, **kv: Any) -> Logger: ...
    def enabled(self, level: str) -> bool: ...   # level — имя уровня, иное → ValueError

def new(cfg: Config, *, writer: Writer | None = None, redact: RedactConfig | None = None,
        otel: LoggerProvider | None = None, service: ServiceConfig | None = None) -> Logger
def nop() -> Logger                              # ничего не пишет, enabled → False
def capture_stdlib(log: Logger, cfg: Config) -> None   # корневой logging → log

Attr = Callable[[], object | None]
def register_attr(key: str, fn: Attr) -> None    # дедупликация по ключу

def new_config() -> Config
def new_redact_config() -> RedactConfig
def new_service_config() -> ServiceConfig

class ConfigError(ValueError): ...               # невалидный уровень, формат, regex или замена
class Writer(Protocol): write(str); flush()      # sys.stdout, файл, io.StringIO

OptionalRedactConfig = RedactConfig | None       # ключи override-зависимостей LogkitProvider
OptionalLoggerProvider = LoggerProvider | None
class LogkitProvider(Provider): ...              # provides Logger; Config не provide'ит
DEFAULT_PATTERNS: tuple[str, ...]
```

Цепочка: redact → метрика → ctx-поля → fan-out (локальный sink с
`trace_id`/`span_id`; OTel-sink).

## Тестирование

Боевой логгер в тесте — `writer=io.StringIO()`:

```python
stream = io.StringIO()
log = logkit.new(logkit.Config(level="debug"), writer=stream)
log.info("hello", k="v")
record = json.loads(stream.getvalue())  # record["msg"], ["level"], ["time"], ["k"]
```

Проверять поля лога кита удобнее capture-логгером
[`testkit/logtest`](../testkit/logtest/) — он реализует `logkit.Logger` и копит
записи в памяти:

```python
from pylibinfra.testkit.logtest import Capture

log = Capture()  # или capture_at("warn")
service.run(log)
assert log.has("job finished")
assert log.find("job finished").str("result") == "ok"
```

OTel-sink проверяется через `opentelemetry.sdk._logs.LoggerProvider` с
`SimpleLogRecordProcessor` и `InMemoryLogRecordExporter`. Сниппеты § Быстрый
старт закреплены `tests/logkit/test_readme.py`.

## Инварианты и подводные камни

- **В production только `logkit.Logger`.** `print()` и `logging.getLogger()`
  обходят trace-correlation, redact, метрику и единый формат.
- **Ctx-поля всегда на верхнем уровне записи** и идут **мимо redact'а**:
  `register_attr`-значения, `request_id`, `trace_id`/`span_id` добавляются после
  redact-слоя. Экстрактор, отдающий PII, маскирует на своей стороне. Одноимённое
  поле вызова ctx-поле перекрывает. Исключение из экстрактора запись не роняет —
  поле просто пропускается.
- **Имена самой записи полем вызова не перекрываются.** `time`, `level`, `msg`,
  `source` (при `LOG_SOURCE=true`) и `trace_id`/`span_id` (внутри спана) в
  локальном sink'е принадлежат записи: одноимённое поле вызова туда не попадает.
  В OTel-sink'е конкуренции нет — там это атрибуты, и поле сохраняется.
  `msg` — позиционный параметр, поэтому `log.info("...", msg="…")` пишет поле, а
  не подменяет сообщение.
- **Опции `service=` — тоже мимо redact'а**: это статическая идентичность
  процесса, а не данные запроса.
- **Redact выключен по умолчанию** и применяется до fan-out: маскируются оба
  sink'а. Маска работает по строкам — `msg`, строковые значения полей (в т.ч. из
  `with_`), содержимое вложенных dict/list/tuple, текст исключений. Прочие
  объекты маскируются по своему строковому представлению, и тип меняется только
  при реальном совпадении. Числа не трогаются, поэтому unix-ms и длительности
  остаются числами.
- **`with_`-поля маскируются один раз, на привязке**, а не на каждой записи.
- **`LOG_SILENT=true` без OTel-sink'а = записи никуда не идут** (метрика при
  этом считается).
- **Ошибка конфигурации — при конструировании логгера**, не на первой записи:
  невалидный `LOG_LEVEL`/`LOG_FORMAT`/regex/замена валят старт. Прямой
  `Config(...)` даёт pydantic `ValidationError` (тоже `ValueError`), а
  `new_config()` — `logkit.ConfigError`.
- **`register_attr` глобален** и идемпотентен по ключу (последняя регистрация
  побеждает); зовите на старте.
- **`capture_stdlib` меняет глобальный `logging`**: снимает root-handler'ы и
  ставит свой (повторный вызов не даёт дублей). Библиотеки, пишущие в свои
  логгеры с `propagate=False` (uvicorn), доходят до него только после того, как
  их логгеры вернут propagate. Сбой доставки записи наружу не поднимается — его
  забирает `logging.Handler.handleError`.
- **`LOG_LEVEL=debug` вместе с OTLP-sink'ом даёт самоподдерживающийся трафик**:
  HTTP-клиент экспортёра (`urllib3`) пишет debug-записи о каждом батче, они
  уходят тем же sink'ом в следующий батч. Диагностику самого SDK (логгер
  `opentelemetry`) `telemetry` уводит мимо OTLP, `urllib3` — нет: держите
  `debug` точечно или глушите `logging.getLogger("urllib3").setLevel(INFO)`.
- **Порядок регистрации провайдеров в dishka значим**: `RedactConfig` и
  OTel-sink подхватываются, только если их провайдер зарегистрирован **после**
  `LogkitProvider`.
- **Правка `logkit` — breaking для всех сервисов**: формат записи, имена ctx-полей
  и `Logger` — часть корп obs-контракта.
