# timerkit

Периодические job'ы поверх asyncio. Сервис описывает задачу структурой `Job` и
складывает такие структуры в кортеж `Jobs`; `TimerkitProvider` поднимает `Runner`,
который на старте валидирует набор, крутит каждую job в своей задаче, а на закрытии
контейнера **дожидается** идущих запусков в пределах дедлайна.

Каждый запуск открывает свой root-спан, исключение внутри `run` логируется и не
прерывает цикл, исход и длительность учитываются метриками.

## Импорт

```python
from pylibinfra.timerkit import Job, Jobs, Runner, TimerkitProvider
```

Версия и установка — [корневой README](../../../README.md).

## Быстрый старт

Кит-источник периодики отдаёт фабрику `Job`, App-Provider консьюмера складывает их
в `Jobs`:

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.logkit import Logger, nop
from pylibinfra.timerkit import Job, Jobs, Runner, TimerkitProvider


def cleanup_job(service: Service) -> Job:
    return Job(
        name="expired-sessions-cleanup",
        interval=300,  # секунды между запусками, > 0
        timeout=60,  # дедлайн одного запуска; 0 — без дедлайна
        jitter=30,  # случайная добавка [0, jitter) к ожиданию
        run_on_start=True,  # первый запуск сразу, а не через interval
        run=service.purge_expired_sessions,
    )


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def jobs(self, service: Service) -> Jobs:
        return (cleanup_job(service),)

    @provide
    def log(self) -> Logger:
        return nop()  # в приложении — LogkitProvider


# AppProvider идёт ПОСЛЕ TimerkitProvider: он перекрывает пустой дефолт Jobs.
container = make_async_container(TimerkitProvider(), AppProvider())

runner = await container.get(Runner)  # старт: dishka строит ресурс лениво
...
await container.close()  # остановка: ждём идущие запуски
```

**`Runner` нужно получить из контейнера явно** — dishka ничего не строит без
запроса, и без `await container.get(Runner)` расписание не поднимется.

Без DI:

```python
runner = Runner(jobs, log)
runner.start()
...
await runner.stop()
```

### Жизненный цикл

- **start** — валидирует все job'ы (ошибка называет job и не даёт стартовать) и
  поднимает задачу на каждую. Повторный `start` при живых задачах — no-op.
- **Ожидание** отсчитывается от **конца** предыдущего запуска: `interval` +
  случайные `[0, jitter)`. Запуски одной job не перекрываются.
- **stop** — сигналит остановку, ждёт идущие запуски до `timeout` (по умолчанию
  `DEFAULT_STOP_TIMEOUT = 15` с), не уложившиеся отменяет, пишет
  `timerkit: stop: jobs still running` и учитывает их как `result="cancelled"`.
  После `stop` тот же `Runner` можно запустить снова.

## Конфигурация

ENV-переменных кит не читает — всё задаётся полями `Job`.

| Поле | Тип | Default | Правило |
|---|---|---|---|
| `name` | `str` | — | Обязательно, уникально в наборе; идёт в логи, спаны и label `job`. |
| `interval` | `float`, секунды | — | `> 0`. Пауза между запусками. |
| `run` | `Callable[[], Awaitable[None]]` | — | Обязательно. Исключение логируется и не прерывает цикл. |
| `timeout` | `float`, секунды | `0.0` | `>= 0`. При `> 0` запуск идёт под `asyncio.timeout`. |
| `jitter` | `float`, секунды | `0.0` | `>= 0`. Размазывает запуски однотипных job'ов между инстансами. |
| `run_on_start` | `bool` | `False` | Прогон сразу на старте (не блокирует получение `Runner`). |

Расписание идёт по `clockkit.Clock` из контейнера: по нему считается ожидание
между запусками и длительность запуска. Дефолта у кита нет — в приложении его даёт
`clockkit.ClockProvider()` (`Real`), тест регистрирует свой Provider с `Tickable`. `timeout` — это
`asyncio.timeout`, он идёт по реальному времени и от подменённых часов не зависит.

## Observability

Спаны — через `otelkit.tracer("timerkit")` (no-op без `TracerProvider`):

| Спан | Kind | Атрибуты | Статус |
|---|---|---|---|
| `timerkit.job <name>` | internal | `timer.job` = `Job.name` | `Error` + `record_exception` на исключении `run`; `Error` c описанием `cancelled` (без события) на отмене запуска по дедлайну `stop` |

Спан — корень трейса запуска: всё, что инструментировано внутри `run` (pgkit,
httpclient, natskit), становится его детьми, а не сиротскими трейсами.

Метрики — через `otelkit.meter("timerkit")` (no-op без `MeterProvider`):

| Метрика | Тип | Labels | Что |
|---|---|---|---|
| `timer_job_runs_total` | counter | `job`, `result` (`ok` / `error` / `cancelled`) | Запуски `run` по исходу |
| `timer_job_duration_seconds` | histogram (s, общие бакеты) | `job` | Длительность одного запуска |

Логи (`logkit.Logger`):

| Событие | Уровень | Сообщение | Поля |
|---|---|---|---|
| `run` бросил исключение | error | `timerkit: job error` | `job`, `error` |
| Запуски не уложились в дедлайн остановки | error | `timerkit: stop: jobs still running` | `jobs`, `timeout` |

Успешные запуски не логируются; запись пишется внутри спана запуска и несёт его
`trace_id`.

## API

```python
@dataclass(frozen=True, slots=True)
class Job:
    name: str
    interval: float
    run: Callable[[], Awaitable[None]]
    timeout: float = 0.0
    jitter: float = 0.0
    run_on_start: bool = False

Jobs = tuple[Job, ...]

class Runner:
    def __init__(self, jobs: Jobs, log: Logger, *, clock: Clock | None = None) -> None
    def start(self) -> None
    async def stop(self, timeout: float = DEFAULT_STOP_TIMEOUT) -> None

def validate(jobs: Jobs) -> None       # правила Job; проблема — JobError
class JobError(ValueError)
class TimerkitProvider(Provider)       # Jobs + Logger + Clock → Runner
DEFAULT_STOP_TIMEOUT = 15.0
```

Кит, которому нужна периодика (`bruteforcekit` cleanup, `outboxkit` publisher и
partition-keeper), отдаёт фабрику `..._job(...) -> timerkit.Job` с именем как в
golibinfra (`bruteforcekit.cleanup`, `outboxkit.publisher.<instance>`,
`outboxkit.partition-keeper.<table>`); консьюмер складывает их в `Jobs`.

## Тестирование

Тесты кита не спят: часы — `clockkit.Tickable`, пробуждение происходит внутри
`advance`, а `wait_sleepers` снимает гонку «задача ещё не заснула»:

```python
clock = Tickable(datetime(2026, 1, 1, tzinfo=UTC))
runner = Runner((Job(name="tick", interval=60, run=job.run),), nop(), clock=clock)

runner.start()
await clock.wait_sleepers(1)  # цикл ждёт первый интервал
clock.advance(60)
await clock.wait_sleepers(1)  # запуск прошёл, цикл ждёт следующий
await runner.stop()
```

Свою периодику тестируйте на уровне `run` — это обычная `async def` без аргументов;
расписание вокруг неё проверяется подстановкой `Tickable`. Метрики и спаны читаются
общими фикстурами репозитория (`read_metric`, `span_exporter`).

## Инварианты и подводные камни

- **Валидация — на старте.** Пустое `name`, `run is None`, `interval <= 0`,
  отрицательные `timeout`/`jitter`, дубль имени → `JobError` из `start()`,
  приложение не поднимается.
- **`Runner` резолвится явно.** dishka строит ресурсы лениво: без
  `await container.get(Runner)` job'ы не запускаются (аналог `fx.Invoke` в
  golibinfra, которого в dishka нет).
- **`run` обязан завершаться.** На остановке идущий запуск не убивается сразу:
  `stop` ждёт его до дедлайна, затем отменяет, пишет error-лог и считает запуск
  как `result="cancelled"`. Собственный дедлайн запуска — `timeout`.
- **`start` идемпотентен.** Повторный вызов при живых задачах ничего не делает:
  иначе job крутилась бы в двух задачах, а первое поколение осталось бы вне
  `stop`. После `stop` `start` поднимает расписание заново.
- **Ожидание — от конца предыдущего запуска**, поэтому фактический период =
  `interval` + длительность `run` (+ jitter). Пропущенных тиков и наложений нет.
- **Исключение не валит процесс**, но это страховка, а не обработка ошибок:
  ошибки обрабатывайте внутри `run`.
- **`result="panic"` в Python нет.** В golibinfra panic отделён от ошибки; здесь
  необработанное исключение идёт как `result="error"`. Отдельный исход
  `result="cancelled"` — запуск, снятый по дедлайну `stop` (в Go горутину не
  отменить, там такого исхода нет).
- **Backoff/retry/отключения «сломанной» job у кита нет** — реализуйте внутри `run`.
