# healthkit

Именованная health-проба. Киты с внешними зависимостями (`pgkit`, `natskit`,
`cachekit`, `storagekit/s3`) отдают `healthkit.Probe`; консьюмер складывает пробы
в кортеж `healthkit.Probes`, `httpkit` отвечает по ним на `/ready` (503, если хоть
одна упала), `grpckit` переоценивает по ним статус `grpc.health.v1`.

## Импорт

```python
from pylibinfra import healthkit
```

Версионирование и установка — в [корневом README](../../../README.md).

## Быстрый старт

```python
from dishka import Provider, Scope, provide

from pylibinfra import cachekit, healthkit, natskit, pgkit


class AppProbes(Provider):
    scope = Scope.APP

    @provide
    def probes(self, db: pgkit.DB, conn: natskit.Conn, cache: cachekit.Cache) -> healthkit.Probes:
        return (pgkit.probe(db), natskit.probe(conn), cachekit.probe(cache))
```

Своя проба — имя плюс проверка:

```python
async def check_sync() -> None:
    await sync.lag()  # ошибка = проба упала


probe = healthkit.new("hub-sync", check_sync)
```

`HttpProvider` и `GrpcProvider` берут `healthkit.Probes` из контейнера; дефолт кита —
пустой кортеж, провайдер консьюмера регистрируется после них и перекрывает его.

## Конфигурация

Конфига нет: `healthkit` — dataclass, две функции и тип-алиас без состояния и без
`Provider`. Таймаут пробы на `/ready` задаёт `httpkit` (`HTTP_READY_PROBE_TIMEOUT`),
в health-loop gRPC — `grpckit` (`GRPC_HEALTH_PROBE_TIMEOUT`), применяя
[`with_timeout`](#with_timeout).

## API

```python
Check = Callable[[], Awaitable[None]]

@dataclass(frozen=True, slots=True)
class Probe:
    name: str
    check: Check
    async def run(self) -> None

def new(name: str, check: Check) -> Probe
def with_timeout(probe: Probe, seconds: float) -> Probe
class ProbeTimeoutError(TimeoutError)   # .name, .seconds

Probes = tuple[Probe, ...]
```

### Probe

`name` — идентификатор зависимости в логе `/ready` (`pgkit`, `natskit/events`,
`storagekit`). `check` — сама проверка; ошибку она префиксует именем своего кита
(`pgkit: ping: …`), чтобы текст в логе был читаем и без `name`.

### run

Выполняет `check` и поднимает его исключение как есть.

### with_timeout

Ограничивает время одной проверки: просроченная отменяется, а `run` поднимает
`ProbeTimeoutError` с именем пробы и дедлайном. Завершения отменённой проверки
`run` **не ждёт** — проба, глотающая `CancelledError`, не держит `/ready` дольше
дедлайна, но её задача живёт до собственного конца. Отмена самого `run` отменяет
и проверку. `seconds <= 0` возвращает пробу без изменений. Имя пробы сохраняется.

### Probes

DI-ключ: кортеж собирает App-Provider консьюмера (аналог fx-группы
`healthkit.probes` в golibinfra). Киты в контейнер пробы не кладут — только
фабрики `probe(...)`.

## Observability

Своих метрик и спанов у кита нет. Результат проб виден в `httpkit`: код ответа
`/ready`, тело со статусом каждой пробы по имени и warn-лог
`readiness probe failed` с полями `probe` (значение `Probe.name`) и `err`.

## Тестирование

```python
async def test_ready_fails_on_down_dependency() -> None:
    async def down() -> None:
        raise RuntimeError("pgkit: ping: connection refused")

    probe = healthkit.with_timeout(healthkit.new("pgkit", down), 1.0)
    with pytest.raises(RuntimeError):
        await probe.run()
```

## Инварианты

- **Агрегация проб — не здесь.** `healthkit` не запускает набор проб: `httpkit`
  прогоняет все пробы кортежа параллельно, логирует каждую упавшую и отвечает
  `503`, если упала хотя бы одна. Второго контракта с другой семантикой кит не
  заводит.
- **Проба не должна быть дорогой.** `/ready` дёргают оркестраторы каждые
  несколько секунд; используйте `PING`/`SELECT 1`, не бизнес-запросы.
- **Таймаут снаружи.** `Probe` сама по себе без дедлайна; транспортный кит
  оборачивает её `with_timeout` со своим конфигом.
- **Проба обязана уважать отмену.** `with_timeout` вернёт управление в срок и без
  этого, но задача зависшей проверки живёт до её собственного завершения — и
  держит соединение, которое она заняла.
