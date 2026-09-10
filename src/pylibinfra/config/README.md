# config

Загрузчик конфигов приложения: модель pydantic-settings заполняется ENV-слоем и
источниками-override'ами (`Source`) поверх него. Нужен там, где приложению мало
голого окружения: единый порядок приоритета, приставка окружения на инстанс и
возможность добавить свой источник (секрет-стор, файл, remote) без правки китов.

## Граница

Кит pylibinfra читает окружение сам: `<kit>.new_config()` строит свою
`KitSettings` напрямую и **проходит мимо** `config.Source`. Кастомный источник
действует только на модели, переданные в `config.load`. Подключён кит через
`new_config()` — его значения придут из ENV мимо источников.

## Импорт

```python
from pylibinfra.config import ConfigError, Source, env_prefixed, load
```

Версия и установка — [корневой README](../../../README.md).

## Быстрый старт

`load` заполняет **одну** модель: ENV-слой pydantic-settings плюс источники поверх.
Типовая проводка — App-Provider, который отдаёт китам их конфиги:

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import config
from pylibinfra.flagkit import Config as FlagConfig, FlagkitProvider
from pylibinfra.grpckit import Config as GrpcConfig


class VaultSource:
    """Свой источник: кладёт значения под env-именами полей модели."""

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def apply(self, dst: dict[str, object]) -> None:
        dst.update(self._secrets)


class AppProvider(Provider):
    scope = Scope.APP

    def __init__(self, secrets: dict[str, str]) -> None:
        super().__init__()
        self._sources = (VaultSource(secrets), config.env_prefixed("myapp"))

    @provide
    def grpc_config(self) -> GrpcConfig:
        return config.load(GrpcConfig, *self._sources)

    @provide
    def flags_config(self) -> FlagConfig:
        return config.load(FlagConfig, *self._sources)


container = make_async_container(FlagkitProvider(), AppProvider(secrets))
```

Порядок аргументов = порядок приоритета: поздний источник перезатирает ранний, а
любой источник — ENV-слой (значения источников идут в модель init-kwargs'ами, они
приоритетнее переменных окружения).

Собственные значения приложения (не из китов) описываются такой же моделью:

```python
from pydantic import Field

from pylibinfra._internal.configkit import KitSettings


class AppConfig(KitSettings):
    tenant: str = Field(default="", validation_alias="APP_TENANT")


app = config.load(AppConfig, config.env_prefixed("myapp"))
```

### Модель на модель не вкладывается

`load` не спускается в поля-подмодели: `KitSettings`-поле заполняет себя из ENV
само (обычно через `default_factory`), и значения источников до него не доходят —
ключи источника адресуют алиасы **той** модели, которую передали в `load`.
Поэтому конфиг каждого кита грузится своим вызовом `load`, как в примере выше.

## Конфигурация

Собственных ENV-переменных кит не читает: он прогоняет через pydantic-settings
модели, которые ему передали. Имена переменных задаёт каждый кит своими
`validation_alias`'ами (их стабильность — корневой README § Стабильность API).

Конфиги китов наследуют `configkit.KitSettings`, поэтому окружение читается
**только по алиасам**: голое имя поля (`addr`, `definitions`, `secret`) переменной
окружения не соответствует, и `PATH`/`HOME`/`SECRET` в конфиг не заезжают.
`.env`-файлы и docker secret-файлы киты не читают — источник значений один:
окружение процесса плюс то, что положил `Source`.

`env_prefixed(prefix)` копирует в модель все переменные `<PREFIX>_*` под именем без
приставки: `MYAPP_GRPC_ADDR=:9091` заполняет поле с алиасом `GRPC_ADDR`. Не заданная
с приставкой переменная берётся из голого имени — его читает базовый ENV-слой.
Приставка нормализуется: регистр и обрамляющие `_` не важны, пустая — `ConfigError`.

Граница источника: он видит только окружение, не модель, поэтому кладёт значения
под **env-именем**. Поле без `validation_alias` (у китов такие только вложенные
конфиги) окружение не читает вовсе — источник заполнит его, лишь положив значение
под именем поля. Модель должна игнорировать лишние ключи (`extra="ignore"` — так
настроен `configkit.settings_config()` у всех конфигов китов).

## Observability

Кит **ничего не эмитит**: ни метрик, ни спанов, ни логов — это разбор окружения в
модель, без I/O и фонового состояния.

## API

```python
class Source(Protocol):
    def apply(self, dst: dict[str, Any]) -> None: ...

def load[T: BaseModel](model: type[T], *sources: Source) -> T
def env_prefixed(prefix: str) -> Source
class ConfigError(configkit.ConfigError)
```

| Символ | Роль |
|---|---|
| `load` | Строит модель: ENV-слой + значения источников по порядку. |
| `Source` | Порт источника: заполняет `dst` под env-именами (алиасами полей модели). |
| `env_prefixed` | Источник ENV с приставкой (`<PREFIX>_<NAME>` перекрывает `<NAME>`). |
| `ConfigError` | Ошибка источника или валидации модели (подкласс `ValueError`). |

`load` применяет **все** источники, даже если ранний упал: ошибки собираются в один
`ConfigError` со строкой `source[i]: …` на каждый упавший — деплой видит все
проблемы разом, а не по одной за перезапуск. При ошибке источников модель не
строится. Ошибка валидации модели перечисляет локации полей и сообщения **без
значений** — DSN и токены в текст ошибки не попадают.

## Тестирование

- **ENV-слой** — `monkeypatch.setenv` + `load(Model)`.
- **Свой источник** — класс в три строки с методом `apply`; ошибка внутри `apply`
  проверяется на тексте `config: source[0]: …`.
- **Приставка** — `monkeypatch.setenv("MYAPP_GRPC_ADDR", ...)` + `load(Model,
  env_prefixed("myapp"))`.

## Инварианты и подводные камни

- **Приоритет = порядок аргументов.** Источники применяются слева направо, поздний
  перезатирает раннего; все они перекрывают ENV-слой.
- **Ошибки источников не останавливают загрузку** — отработают все, вернётся общая
  ошибка; модель в этом случае не строится вовсе.
- **Значения полей не попадают в текст ошибки** от pydantic: перечисляются только
  локации и сообщения. Текст исключения источника кит передаёт как есть — не
  кладите в свои сообщения то, что нельзя показать в логе деплоя.
- **Киты не зависят от `Source`.** `<kit>.new_config()` читает ENV напрямую;
  источники действуют только на модели, переданные в `load`.
- **`Source` кладёт значения под env-именем**, а не под именем поля — источник не
  знает модель.
