# flagkit

Простейшие feature flags из ENV. Одна переменная `FEATURE_FLAGS=foo=true,bar=baz`
разбирается в `Store`, который отдаёт значения по имени через `bool`/`str`/`names`.
Без remote-источников и динамического refresh: набор флагов фиксируется при старте
процесса, изменение флага требует рестарта.

Подходит для kill-switch'ей и статических переключателей фич, которым не нужна
живая перезагрузка. Разбор строгий: невалидная запись роняет старт `ConfigError`,
а не выключает флаг молча.

## Импорт

```python
from pylibinfra.flagkit import FlagkitProvider, Store, new_config
```

Версия и установка — [корневой README](../../../README.md).

## Быстрый старт

`FlagkitProvider` отдаёт `Store`; `Config` регистрирует консьюмер (asymmetry note) —
дешёвый режим `new_config()`:

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.flagkit import Config, FlagkitProvider, Store, new_config


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def flags_config(self) -> Config:
        return new_config()  # FEATURE_FLAGS из ENV


container = make_async_container(FlagkitProvider(), AppProvider())
flags = await container.get(Store)

if flags.bool("new_checkout"):
    ...
region = flags.str("region")
```

Без DI:

```python
from pylibinfra.flagkit import EnvStore, new_config

flags = EnvStore(new_config())
```

При `FEATURE_FLAGS=new_checkout=true,region=eu`:

- `flags.bool("new_checkout")` → `True`
- `flags.str("region")` → `"eu"`
- `flags.names()` → `["new_checkout", "region"]`
- `flags.bool("unknown")` → `False`, `flags.str("unknown")` → `""`

## Конфигурация

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `FEATURE_FLAGS` | `Config.definitions` | `str` | `""` | Определения флагов. Каждая непустая запись — `name=value` с непустым `name`; пустая строка валидна |

`flagkit.Config` — **public API**: имя поля и его `validation_alias` меняются только
мажорным апгрейдом. Окружение читается только по алиасу (`configkit.KitSettings`):
переменная с именем поля (`definitions`) конфиг не заполняет.

### Формат значения `FEATURE_FLAGS`

```
FEATURE_FLAGS=foo=true,bar=baz,booltrue=1
```

- Записи разделяются запятой `,`.
- Имя и значение разделяет **первый** `=`; всё после него — значение (включая `=`).
- Вокруг записи, имени и значения обрезаются пробелы.
- Пустая запись (подряд идущие запятые, запятая в конце) пропускается.
- Запись без `=` — `ConfigError`.
- Запись с пустым именем (`=value`) — `ConfigError`.
- Ошибки всех невалидных записей перечисляются в одном сообщении.
- Пустая (или незаданная) `FEATURE_FLAGS` даёт пустой `Store` без ошибки.
- Имена регистрозависимы: `FOO=true` не отвечает на `bool("foo")`.
- При повторе имени побеждает **последняя** запись: `foo=true,foo=false` →
  `bool("foo") is False`.
- Кавычки не снимаются: `foo="true"` даёт `str("foo") == '"true"'` и
  `bool("foo") is False`.
- Разделитель `;` синтаксически неотличим от значения: `foo=true;bar=baz` — это один
  флаг `foo` со значением `true;bar=baz`. Разделяйте запятой.

## Observability

Кит **ничего не эмитит**: ни метрик, ни спанов, ни логов — чтение флага это доступ к
in-memory словарю. Активную конфигурацию логируйте сами через `Store.names()`.

## API

```python
class Store(Protocol):
    def names(self) -> list[str]
    def bool(self, name: str) -> bool
    def str(self, name: str) -> str

class EnvStore:  # реализация Store поверх Config
    def __init__(self, cfg: Config) -> None

def new_config() -> Config
class Config(KitSettings)               # definitions ← FEATURE_FLAGS
class ConfigError(configkit.ConfigError)
class FlagkitProvider(Provider)         # → Store
```

| Символ | Роль |
|---|---|
| `Store` | Зависимость бизнес-кода: `bool` для переключателя, `str` для значения-конфига, `names` для лога. |
| `EnvStore` | Реализация `Store`; конструктор валидирует определения (`ConfigError`). |
| `new_config` | `Config` из ENV + валидация формата; `Provider` его не отдаёт. |
| `ConfigError` | Невалидная `FEATURE_FLAGS` (подкласс `configkit.ConfigError` → `ValueError`). |
| `FlagkitProvider` | dishka-Provider: `Config` → `Store`. |

`bool` истинен ровно для значений `1`, `t`, `T`, `TRUE`, `true`, `True` (набор
`strconv.ParseBool` golibinfra) — один и тот же `.env` даёт одинаковые
kill-switch'и в Go- и Python-сервисах. Любое другое значение (`yes`, `on`, `TrUe`,
`0`, пустое) — `False`.

## Тестирование

Стор создаётся напрямую, без ENV и контейнеров:

```python
store = EnvStore(Config(definitions="foo=true,bar=baz"))
store.bool("foo")  # True
store.str("bar")  # "baz"
store.bool("unknown")  # False
```

`Config(definitions=...)` в обход `new_config()` поднимает pydantic
`ValidationError` (тоже `ValueError`) на невалидной строке; `new_config()` и
`EnvStore` дают `flagkit.ConfigError`. Свой фейк не нужен — `Store` это Protocol,
подойдёт любой объект с тремя методами.

## Инварианты и подводные камни

- **Снимок на старте.** Флаги читаются один раз при конструировании `Store`;
  изменение `FEATURE_FLAGS` применяется после рестарта процесса.
- **Строгий разбор.** Опечатка в формате останавливает старт. Опечатка в *имени*
  флага не диагностируется: неизвестное имя всегда даёт дефолт.
- **Дефолт при отсутствии флага.** `False` для `bool`, `""` для `str`. «Флаг задан
  как false» и «флага нет» неразличимы — различает `names()`.
- **Типизация — на стороне читателя.** Стор хранит строки; числа и списки парсит
  консьюмер из `str`.
- **Стор иммутабелен** — словарь заполняется в конструкторе, конкурентное чтение
  безопасно без синхронизации.
- **`Provider` не отдаёт `Config`** — как и у остальных китов, его регистрирует
  консьюмер.
