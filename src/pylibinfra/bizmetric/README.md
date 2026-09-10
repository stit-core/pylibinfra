# bizmetric

Конвенциональный helper для бизнес-метрик: тонкая обёртка над OTel-инструментами,
которая форсит единую форму имени `biz_<domain>_<name>_<suffix>` и валидирует
`domain`/`name`. Кривое имя ловится `InvalidNameError` при регистрации инструмента —
на старте сервиса, а не в проде кривой серией.

Префикс `biz_` даёт единый автодискавер бизнес-метрик (`{__name__=~"biz_.*"}`) без
ручного списка. Helper делает конвенцию неизбежной: собрать имя в обход нельзя —
`must_name` валидирует его тем же правилом, что и фабрики инструментов.

## Импорт

```python
from pylibinfra.bizmetric import counter, gauge, histogram, up_down_counter
```

Версия и установка — [корневой README](../../../README.md).

## Быстрый старт

`meter` консьюмер берёт из своего OTel-SDK (`metrics.get_meter("<service>")`); без
сконфигурированного `MeterProvider` инструменты — no-op. `domain` и `name` — два
уровня имени, префикс и суффикс подставляет helper.

```python
from opentelemetry import metrics

from pylibinfra.bizmetric import counter, gauge, histogram, up_down_counter

meter = metrics.get_meter("shop")

logins = counter(meter, "auth", "login_attempts")  # biz_auth_login_attempts_total
logins.add(1, {"result": "ok"})

onboarding = histogram(meter, "onboarding", "duration")  # biz_onboarding_duration_seconds
onboarding.record(1.5)

carts = gauge(meter, "checkout", "active_carts")  # biz_checkout_active_carts
carts.set(42)

open_carts = up_down_counter(meter, "checkout", "carts_open")  # biz_checkout_carts_open
open_carts.add(1)
```

DI-проводки у кита нет: это набор stateless-функций, инструменты заводятся в
конструкторе сервиса, которому они принадлежат.

## Конфигурация

Конфига нет — ENV-переменных кит не читает и `Provider` не отдаёт.

## Observability

Собственных сигналов кит не эмитит: он только собирает инструменты на переданном
meter'е. Что получится на выходе:

| Фабрика | OTel-инструмент | Суффикс | Unit | Пример имени |
|---|---|---|---|---|
| `counter` | `create_counter` | `_total` | `{event}` | `biz_auth_login_attempts_total` |
| `histogram` | `create_histogram` | `_seconds` | `s` | `biz_onboarding_duration_seconds` |
| `gauge` | `create_gauge` | — | `{item}` | `biz_checkout_active_carts` |
| `up_down_counter` | `create_up_down_counter` | — | `{item}` | `biz_checkout_carts_open` |

`histogram` получает общие для репозитория границы бакетов (`0.005 … 30` секунд, те
же, что у `_seconds`-метрик китов) — квантили по всем гистограммам считаются по одной
сетке.

Значения unit в фигурных скобках (`{event}`, `{item}`) — аннотации по
OTel-спецификации: OTLP-native бэкенды показывают их как есть, Prometheus-трансляция
отбрасывает и имени метрики не меняет. Константы — `UNIT_EVENT`, `UNIT_ITEM`,
`UNIT_SECONDS`.

Размерности метрики задаются атрибутами в момент записи, а не именем:

```python
logins.add(1, {"result": "ok", "method": "password"})
```

Идентичность сервиса (`service.name`, `service.version`,
`deployment.environment.name`) в атрибутах не дублируется — её несёт OTel-resource
из проводки SDK консьюмера.

## API

```python
def counter(meter, domain, name, *, description="", unit=UNIT_EVENT) -> Counter
def histogram(meter, domain, name, *, description="") -> Histogram
def gauge(meter, domain, name, *, description="", unit=UNIT_ITEM) -> Gauge
def up_down_counter(meter, domain, name, *, description="", unit=UNIT_ITEM) -> UpDownCounter
def must_name(domain: str, name: str) -> str

UNIT_EVENT = "{event}"; UNIT_ITEM = "{item}"; UNIT_SECONDS = "s"
class InvalidNameError(ValueError)
```

| Символ | Роль |
|---|---|
| `counter` | Монотонный счётчик событий (`add(1, attrs)`). |
| `histogram` | Длительность в секундах (`record(seconds)`); бакеты — общие для репозитория. |
| `gauge` | Текущее значение величины (`set(value)`) — когда код знает величину целиком. |
| `up_down_counter` | Дельты (`add(+1)` / `add(-1)`) — когда код видит только изменения. |
| `must_name` | Имя `biz_<domain>_<name>` без создания инструмента — для observable-gauge и других ручных регистраций. |
| `InvalidNameError` | Нарушение конвенции имени (подкласс `ValueError`). |

`histogram` бакеты не параметризует: длительности всех китов и сервисов лежат на
одной сетке. Нужна своя — заводите инструмент напрямую, имя соберите `must_name`.

## Тестирование

Метрики читаются in-memory reader'ом OTel SDK — контейнеры не нужны:

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

reader = InMemoryMetricReader()
meter = MeterProvider(metric_readers=[reader]).get_meter("test")
counter(meter, "auth", "login_attempts").add(1)
```

Общие фикстуры репозитория (`metric_reader`, `read_metric`) делают то же самое для
кита, который эмитит метрики сам.

## Инварианты и подводные камни

- **`InvalidNameError` при нарушении конвенции.** `domain`/`name` вне
  `^[a-z][a-z0-9_]*$` (заглавные, дефис, пустая строка, цифра первой), с префиксом
  `biz_` или зарезервированным суффиксом (`_total`, `_seconds`, `_count`, `_sum`,
  `_bucket`, `_created`) — исключение при регистрации. Инструменты заводятся на
  старте, поэтому нарушение всплывает там же.
- **Не собирать имя в обход helper'а.** Прямая регистрация OTel-инструмента с
  произвольной строкой ломает конвенцию; ручной случай — `must_name`.
- **Суффиксом считается окончание строки.** `_` внутри `name` допустим
  (`login_attempts`, `count_of_items`), запрещено только окончание из списка.
- **`domain` vs `name`.** `domain` — бизнес-область (`auth`, `checkout`), `name` —
  событие или величина (`login_attempts`, `active_carts`). Оба обязательны.
- **Конфликт типа на одном имени.** Имя уникально в пределах meter'а: `gauge` и
  `up_down_counter` дают имя без суффикса и потому конфликтуют между собой на одной
  паре `domain`/`name`.
- **Метрика ≠ лог.** Атрибуты — конечные множества (`result`, `method`); id
  пользователя, ключ заказа и прочие неограниченные значения в атрибуты не кладут.
