# clockkit

Порт системных часов и его production-реализация. Код, чьё поведение зависит от
времени (TTL, окно, расписание), принимает `Clock` и не зовёт `datetime.now`/
`asyncio.sleep` напрямую — тогда в тестах вместо реального времени подставляются
детерминированные фейки: `Fixed` (застывший момент) и `Tickable` (часы двигает
тест, продвижение будит спящих).

Фейки лежат в этом же пакете, чтобы не плодить отдельный `clocktest`: они —
часть контракта кита, а не тест-хелпер поверх него.

## Импорт

```python
from pylibinfra.clockkit import Clock, ClockProvider, Fixed, Real, Tickable
```

Версия и установка — [корневой README](../../../README.md).

## Быстрый старт

Сервис принимает `Clock` и берёт время только через него:

```python
from datetime import datetime

from pylibinfra.clockkit import Clock, Real


class Session:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock if clock is not None else Real()

    def expired(self, deadline: datetime) -> bool:
        return self._clock.now() > deadline

    async def wait_window(self, seconds: float) -> None:
        # Ожидание тоже через часы: в тесте оно не съест реальные секунды.
        await self._clock.sleep(seconds)
```

В приложении часы приходят из контейнера — `ClockProvider` отдаёт `Clock` как
`Real`:

```python
from dishka import make_async_container

from pylibinfra.clockkit import ClockProvider

container = make_async_container(ClockProvider())
clock = await container.get(Clock)
```

В тесте вместо `Real` подставляется фейк. `Fixed` — когда момент должен быть один и
тот же на всём прогоне:

```python
from datetime import UTC, datetime

clock = Fixed(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
assert clock.now() == clock.now()
```

`Tickable` — когда тест должен явно двигать время и будить ожидающих:

```python
import asyncio

clock = Tickable(datetime(2026, 1, 1, tzinfo=UTC))
session = Session(clock)

task = asyncio.create_task(session.wait_window(30))
await clock.wait_sleepers(1)  # задача уже заснула
clock.advance(30)  # и просыпается здесь же, без реального sleep
await task
```

## Конфигурация

Конфига нет — ENV-переменных кит не читает. `Real` не параметризуется, фейки
настраиваются аргументом конструктора (момент старта).

## Observability

Кит **ничего не эмитит**: ни метрик, ни спанов, ни логов, и не зависит от OTel.
Это чистый примитив над `datetime`/`asyncio`.

## API

```python
class Clock(Protocol):
    def now(self) -> datetime          # текущий момент, aware UTC
    def since(self, t: datetime) -> float   # секунд прошло от t
    async def sleep(self, seconds: float) -> None

class Real: ...                        # datetime.now(UTC) + asyncio.sleep
class Fixed:
    def __init__(self, t: datetime) -> None
class Tickable:
    def __init__(self, start: datetime) -> None
    def advance(self, delta: timedelta | float) -> None
    def set(self, t: datetime) -> None
    def sleepers(self) -> int
    async def wait_sleepers(self, n: int) -> None

class ClockProvider(Provider)          # → Clock = Real
```

| Тип | Роль | Когда |
|---|---|---|
| `Real` | `datetime.now(UTC)`, `asyncio.sleep` | Production; его отдаёт `ClockProvider`. |
| `Fixed` | `now()` всегда один и тот же момент; `sleep(> 0)` не завершается никогда | Тест, где время не должно идти. |
| `Tickable` | `now()` двигает тест; `advance`/`set` будят `sleep` с наступившим дедлайном | Тест на «истёк TTL», «прошёл интервал», «окно закрылось». |

`Tickable` сверх `Clock` даёт:

- `advance(delta)` — сдвинуть часы вперёд (`timedelta` или секунды).
- `set(t)` — поставить часы в `t`, в том числе в прошлое.
- `sleepers()` — сколько `sleep` ждут продвижения.
- `await wait_sleepers(n)` — дождаться, пока ожидающих не станет `n`: снимает гонку
  «задача ещё не заснула». Проснувшийся `sleep` очередь покидает, поэтому
  `wait_sleepers(1)` после `advance` ждёт уже **следующий** сон.

`Clock` — `Protocol` (`runtime_checkable`): подходит любой объект с тремя методами,
наследование не нужно.

### Kit'ы, которые принимают часы

Кит, чьё поведение зависит от времени, берёт `Clock` аргументом конструктора или
опцией `clock=` с дефолтом `Real()`:

- `timerkit` — по нему считается ожидание между запусками job (`Runner(..., clock=)`,
  в контейнере — override `Clock` перед `TimerkitProvider`);
- `cachekit` (`MemoryCache`) — срок жизни ключа;
- любой доменный сервис с TTL/окном — тем же приёмом.

```python
class Service:
    def __init__(self, *, clock: Clock = Real()) -> None:
        self._clock = clock
```

Замер длительности (`since`) — тоже через `Clock`: тогда гистограмма в тесте
детерминирована.

## Тестирование

Детерминизм держится на том, что код нигде не зовёт `datetime.now`/`asyncio.sleep`
сам. Тест подставляет фейк и двигает время явно:

- `Fixed` фиксирует момент — годится для форматирования, сравнения с дедлайном,
  снапшотов. `sleep(seconds > 0)` на нём не завершится: время не движется.
- `Tickable` стоит, пока тест не позовёт `advance`/`set`. Пробуждение происходит
  **внутри** вызова: после `advance` спавшая задача уже готова к запуску.
- `wait_sleepers(n)` перед `advance` снимает гонку с только что созданной задачей.

Реального `time.sleep`/`asyncio.sleep` в тестах кита нет ни одного.

## Инварианты и подводные камни

- **Не звать `datetime.now`/`asyncio.sleep` в обход `Clock`** — прямой вызов ломает
  детерминизм теста, фейк его не перехватывает.
- **`now()` — aware UTC.** `Real` возвращает `datetime.now(UTC)`; фейки — ровно тот
  момент, что им передали, поэтому передавайте aware-`datetime` (`tzinfo=UTC`).
- **`Fixed` действительно застывает** — между двумя `now()` ровно ноль разницы.
- **`Tickable` сам не идёт.** `set` может вернуть часы в прошлое: монотонность
  `Clock` не гарантирует, при движении назад никто не просыпается.
- **`Tickable` двигают откуда угодно.** Очередь спящих под мьютексом, пробуждение
  идёт через цикл самого спящего: в своём цикле `sleep` просыпается внутри вызова
  `advance`/`set`, из другого потока — ближайшим шагом того цикла. `wait_sleepers`
  ждать нужно из цикла, где спят задачи.
- **Отменённый `sleep` покидает очередь** — `sleepers()` после отмены задачи снова
  ноль, `wait_sleepers` не зависнет на «фантомном» спящем.
- **Go-таймеры (`After`/`NewTimer`/`Timer`) в Python не переносятся** — их
  семантику даёт `await clock.sleep(...)` внутри задачи плюс отмена задачи вместо
  `Timer.Stop`.
