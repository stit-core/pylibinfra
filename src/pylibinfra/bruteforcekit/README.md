# bruteforcekit

Guard против brute-force на аутентификационные эндпойнты (login, OTP, reset).
Кит ведёт счётчик неудачных попыток по паре `(subject, ip)` — где `subject` это
логин / email / `phone:+…`, а `ip` адрес клиента — и при достижении порога
выставляет блокировку на заданное окно. Сервис спрашивает guard перед проверкой
пароля (`check`), сообщает ему исход (`record_failure` / `record_success`), а
чистка протухших строк живёт отдельно (`Cleaner.cleanup`) и работает джобой.

Core-абстракции (`Guard`, `Cleaner`, `Config`) отделены от persistence:
протоколы живут в корневом пакете, реализация на Postgres — в подпакете
`pgstore`. Это даёт подменить хранилище в тестах или под другой backend, не
трогая вызывающий код.

## Импорт

```python
from pylibinfra.bruteforcekit import Cleaner, Config, Guard, LockState, new_config
from pylibinfra.bruteforcekit.pgstore import MIGRATIONS, BruteforcePgstoreProvider, cleanup_job
```

Версия и установка — см. [корневой README](../../../README.md).

## Быстрый старт

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import clockkit, timerkit
from pylibinfra.bruteforcekit import Config, Guard, new_config
from pylibinfra.bruteforcekit.pgstore import (
    MIGRATIONS,
    BruteforcePgstoreProvider,
    Store,
    cleanup_job,
)
from pylibinfra.logkit import Config as LogConfig
from pylibinfra.logkit import LogkitProvider, new_config as new_log_config
from pylibinfra.pgkit import (
    Migrated,
    MigrateRunnerConfig,
    MigrationSources,
    PgMigrateProvider,
    PgProvider,
    PoolConfig,
    new_migrate_runner_config,
    new_pool_config,
)


class AuthService:
    def __init__(self, guard: Guard, users: UserRepo) -> None:
        self._guard = guard
        self._users = users

    async def login(self, email: str, ip: str, password: str) -> None:
        lock = await self._guard.check(email, ip)
        if lock is not None:
            raise LockedError(lock.until)  # отдать 429 / 423

        if not await self._users.check_password(email, password):
            lock = await self._guard.record_failure(email, ip)
            if lock is not None:
                raise LockedError(lock.until)
            raise InvalidCredentialsError

        await self._guard.record_success(email, ip)  # успех сбрасывает счётчик


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def pool_config(self) -> PoolConfig:
        return new_pool_config()

    @provide
    def migrate_config(self) -> MigrateRunnerConfig:
        return new_migrate_runner_config()

    @provide
    def bruteforce_config(self) -> Config:
        return new_config()  # BRUTEFORCE_*

    @provide
    def log_config(self) -> LogConfig:
        return new_log_config()  # LOG_*: timerkit.Runner логирует ошибки джоб

    @provide
    def migration_sources(self) -> MigrationSources:
        return (MIGRATIONS,)

    @provide
    def jobs(self, store: Store, cfg: Config) -> timerkit.Jobs:
        return (cleanup_job(store, cfg),)  # без него таблица не чистится

    @provide
    def auth_service(self, guard: Guard, users: UserRepo) -> AuthService:
        return AuthService(guard, users)


container = make_async_container(
    LogkitProvider(),  # timerkit.Runner берёт logkit.Logger из контейнера
    PgProvider(),
    PgMigrateProvider(),
    clockkit.ClockProvider(),  # часы расписания timerkit
    timerkit.TimerkitProvider(),
    BruteforcePgstoreProvider(),
    AppProvider(),
)
await container.get(Migrated)
auth = await container.get(AuthService)
```

Без DI: `Store(db, cfg)` валидирует конфиг и поднимает `bruteforcekit.ConfigError`
на нулевом — guard, который молча никогда не блокирует, хуже упавшего старта.

`ip` берётся из транспорта (`httpkit` отдаёт адрес клиента с учётом
`HTTP_RATE_LIMIT_TRUST_PROXY`). `InvalidKeyError` из любого метода — баг проводки, а не
пользовательская ошибка: отвечайте 500, а не 401.

## Требования к `ip`

Сила guard'а равна доверию к `ip`: если сервис берёт адрес из подделываемого
заголовка, атакующий подставляет новый IP на каждую попытку, каждая попытка
попадает в новую строку и блокировка не наступает никогда.

- **Источник — только доверенный адрес.** Значение `X-Forwarded-For` берётся,
  только когда сервис стоит **за** доверенным прокси. Публичный сервис без
  прокси обязан использовать peer-адрес соединения.
- **Канонизация — на стороне кита.** `Config.normalize_key` приводит адрес к
  канонической форме: `::1` и `0:0:0:0:0:0:0:1`, `2001:DB8::1` и `2001:db8::1`,
  `::ffff:203.0.113.7` и `203.0.113.7` — один бакет счётчика; зона
  (`fe80::1%eth0`) отбрасывается. Приводить адрес заранее не нужно.
- **IPv6 агрегируется по желанию.** По умолчанию (`BRUTEFORCE_IPV6_PREFIX=128`)
  счётчик ведётся на полный адрес; провайдер выдаёт клиенту целую подсеть,
  поэтому для публичных эндпойнтов ставьте `BRUTEFORCE_IPV6_PREFIX=64` — все
  адреса одной `/64` попадают в общий бакет, и перебор адресов подсети не
  обходит блокировку. На IPv4 параметр не влияет.
- **Пустой или неразбираемый `ip` — ошибка.** `check`, `record_failure` и
  `record_success` поднимают `InvalidKeyError`, не выполняя запрос: «ip забыли
  передать» — баг проводки, а не бакет `""` на все адреса subject'а. Адрес с
  портом (`203.0.113.7:443`) и CIDR (`203.0.113.0/24`) тоже отвергаются.

## Требования к `subject`

- Непустой и не длиннее `bruteforcekit.MAX_SUBJECT_LEN` (255 символов) — иначе
  `InvalidKeyError` до запроса. Предел равен ограничению btree-индекса Postgres:
  без него длинный subject ронял бы `record_failure` ошибкой базы, а сервис
  отдавал бы 500 вместо «неверные учётные данные».
- Длину и форму значения (`email ≤ 254`, `phone:+…`) валидирует caller в DTO;
  кит только отсекает заведомо непригодные ключи.
- Регистр и форму subject'а кит не нормализует: `User@example.com` и
  `user@example.com` — разные бакеты. Приводите subject к канонической форме там
  же, где ищете пользователя.

## Конфигурация

`bruteforcekit.Config` — **part of public API**: имена полей и env-alias'ы менять
= breaking change. `new_config()` читает ENV и валидирует значения; нарушение —
`bruteforcekit.ConfigError` (подкласс `ValueError`). Нулевой Config отвергается.
Длительности — **секунды (float)**.

| Env | Поле | Тип | Default | Назначение / правило валидации |
|---|---|---|---|---|
| `BRUTEFORCE_MAX_FAILURES` | `max_failures` | `int` | `5` | Порог: при достижении этого числа неудач в текущем окне пара `(subject, ip)` блокируется; `>= 1`, `1` — блокировка с первой неудачи |
| `BRUTEFORCE_LOCK_DURATION` | `lock_duration` | `float`, секунды | `900` (15m) | Длительность блокировки от момента достижения порога (`locked_until = NOW() + lock_duration`); `> 0` |
| `BRUTEFORCE_CLEANUP_RETENTION` | `cleanup_retention` | `float`, секунды | `86400` (24h) | Возраст строки по `last_attempt`, после которого `cleanup` удаляет её (только если она не под активной блокировкой); `>= 0` |
| `BRUTEFORCE_CLEANUP_INTERVAL` | `cleanup_interval` | `float`, секунды | `300` (5m) | Период cleanup-джобы, он же дедлайн одного прогона; `> 0` |
| `BRUTEFORCE_IPV6_PREFIX` | `ipv6_prefix` | `int` | `128` | Длина префикса, до которой агрегируются IPv6-адреса; `[1, 128]`, `128` — без агрегации, `64` — бакет на `/64`-подсеть |

## Observability

`pgstore.Store` эмитит через глобальные OTel-провайдеры (scope
`pylibinfra.bruteforcekit`); без зарегистрированного `MeterProvider`
инструменты — no-op.

| Метрика | Тип | Labels | Смысл |
|---|---|---|---|
| `bruteforce_checks_total` | counter | `result=allowed\|locked` | Результат `check`: попытка пропущена или упёрлась в активную блокировку |
| `bruteforce_locks_total` | counter | — | Переход пары в заблокированное состояние: до неудачи активной блокировки не было, после неё есть. Продление уже активной блокировки следующими неудачами счётчик не двигает — одно событие на окно |
| `bruteforce_cleanup_deleted_total` | counter | — | Строки, удалённые `cleanup` (суммарно по батчам) |

Ошибки ключа (`InvalidKeyError`) и базы в счётчики не попадают: их видно по
ошибкам вызывающего кода. Спанов и логов кит не заводит — per-query спаны даёт
otel-asyncpg из `pgkit`, метрики пула — `pg_pool_*`.

Всплеск `bruteforce_locks_total` — security-сигнал (распределённая атака или
поломанный клиент); рост `bruteforce_checks_total{result="locked"}` при плоском
`bruteforce_locks_total` — заблокированные пользователи, которые продолжают
стучаться.

## API

### `LockState`

```python
@dataclass(frozen=True, slots=True)
class LockState:
    until: datetime
    failed_count: int = 0
```

Возвращается из `check` и `record_failure`: `None` означает «не заблокировано»,
`LockState` — блокировка активна до `until` по часам базы. `failed_count` —
счётчик неудач текущего окна на момент ответа.

### `Guard`

Core-протокол request-path; реализуется `pgstore.Store`.

```python
class Guard(Protocol):
    async def check(self, subject: str, ip: str) -> LockState | None: ...
    async def record_failure(self, subject: str, ip: str) -> LockState | None: ...
    async def record_success(self, subject: str, ip: str) -> None: ...
```

- `check` — `None`, если строки нет или `locked_until` уже в прошлом по часам
  базы (`locked_until > NOW()` в SQL, часы процесса не участвуют); иначе
  `LockState`. Счётчик не меняет.
- `record_failure` — upsert по `(subject, ip)`: инкремент `failed_count` (или
  `1`, если предыдущая блокировка истекла — это новое окно), обновление
  `last_attempt`; при достижении порога выставляет `locked_until`. Возвращает
  `LockState`, пока блокировка активна. Неучтённая неудача — ошибка, а не
  «попытки ещё есть»: пустой `RETURNING` даёт `BruteforceError`.
- `record_success` — удаляет строку `(subject, ip)`, обнуляя историю попыток.

### `Cleaner`

```python
class Cleaner(Protocol):
    async def cleanup(self) -> None: ...
```

Maintenance-путь, отдельный от `Guard`: его реализуют только хранилища, а фейки
и адаптеры вокруг `Guard` — нет. `cleanup` удаляет строки, которые
разблокированы (`locked_until IS NULL` или уже в прошлом) **и** старше
`cleanup_retention` по `last_attempt`, батчами по `pgstore.CLEANUP_BATCH_SIZE`
(5000) строк до исчерпания. Идемпотентна.

### `Config.normalize_key`

```python
def normalize_key(self, subject: str, ip: str) -> tuple[str, str]  # InvalidKeyError
```

Проверяет subject и приводит `ip` к канонической форме ключа (см. «Требования к
`ip`»). Store зовёт её первым делом в каждом методе — невалидный ключ до базы не
доходит.

### `pgstore.Store`

```python
Store(db: pgkit.DB, cfg: bruteforcekit.Config)
```

Реализация `Guard` и `Cleaner` на одной таблице `bruteforce_attempts` с
составным первичным ключом `(subject, ip)`. Конструктор валидирует `cfg`
(`ConfigError`). Ошибка базы — `pgstore.BruteforceError` с префиксом
`bruteforcekit:`.

### `pgstore.cleanup_job`

```python
def cleanup_job(store: Store, cfg: Config) -> timerkit.Job
```

Джоба `bruteforcekit.cleanup` с интервалом и таймаутом `cleanup_interval`.
Консьюмер кладёт её в `timerkit.Jobs`; первый прогон — через интервал после
старта, схема к этому моменту уже применена.

### `pgstore.BruteforcePgstoreProvider`

dishka-провайдер: один `Store` под обоими портами (`Guard` и `Cleaner`).
`pgkit.DB` и `bruteforcekit.Config` регистрирует caller; `MIGRATIONS` он же
кладёт в `pgkit.MigrationSources`, а `cleanup_job` — в `timerkit.Jobs`.

### Миграции

`pgstore.MIGRATIONS` — `pgkit.Source(name="bruteforcekit")` (таблица версий
`schema_migrations_bruteforcekit`). `PgMigrateProvider` применяет его на старте;
без DI — `migrate_up_all(dsn, MIGRATIONS, ...)`. Таблица:

```sql
CREATE TABLE bruteforce_attempts (
    subject        TEXT        NOT NULL,
    ip             TEXT        NOT NULL,
    failed_count   INT         NOT NULL DEFAULT 0,
    locked_until   TIMESTAMPTZ,
    last_attempt   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (subject, ip)
);
CREATE INDEX bruteforce_attempts_last_attempt_idx ON bruteforce_attempts (last_attempt);
```

`check`, `record_failure` и `record_success` ходят по первичному ключу; индекс по
`last_attempt` обслуживает выборку батча в `cleanup`.

## Тестирование

Интеграционные тесты поднимают реальный Postgres через
`start_postgres(migrations=[MIGRATIONS])`; гейт держит сам helper
(`require_docker`).

Покрытие: блокировка после `max_failures` и с первой неудачи, независимость
бакетов, `check` по часам базы, сброс после `record_success`, новое окно после
истечения блокировки, сохранение активной блокировки при поднятии порога,
канонизация `ip` и агрегация `/64`, атомарность счётчика под конкурентными
`record_failure`, `cleanup` (свежие и заблокированные строки, батчи, индекс
миграции `0002`), метрики, отказ `Store` на невалидном конфиге, сборка провайдера.

Для unit-тестов вызывающего кода без БД `Guard` подменяется фейками из
[`testkit/bruteforcetest`](../testkit/bruteforcetest/): `Permissive` (всегда
пропускает), `Locked(until)` (всегда заблокирован), `Recorder` (запоминает пары
— `attempts()`/`success_attempts()`), `Counting` (та же политика в памяти:
`N` неудач → lock, часы теста через `clockkit.Tickable`).

Контракт ключа фейки держат наравне с `pgstore`: пустой или слишком длинный
`subject`, пустой либо неразбираемый `ip` — `InvalidKeyError` из любого метода, и
в историю `Recorder` такой вызов не попадает. Проводка, которая отдаёт guard'у
пустой `ip`, падает в unit-тесте, а не на проде. Агрегацию IPv6 по
`Config.ipv6_prefix` делает только `Counting` — у остальных фейков конфига нет, и
адрес сравнивается как передан; он же отвергает невалидный конфиг
(`ConfigError`), как конструктор `Store`.

## Инварианты и подводные камни

- **Счётчик ведётся на пару `(subject, ip)`, не отдельно per-subject и per-IP.**
  Ключ строки — составной, поэтому неудачи с одного логина с разных IP считаются
  как разные строки и блокируются независимо; аналогично один IP против разных
  логинов. Если нужны независимые лимиты «N на subject» и «M на IP» — это
  отдельные политики поверх двух вызовов с разными ключами, а не текущая модель.
- **Окно закрывается блокировкой.** `failed_count` растёт до `max_failures`, пара
  блокируется на `lock_duration`; неудачи поверх активной блокировки продлевают
  её, не меняя политику. Как только блокировка истекла, следующая неудача
  открывает **новое** окно (`failed_count = 1`) — один раз попавший под lock
  пользователь не блокируется навсегда одной ошибкой в сутки.
- **Поднятие порога не снимает активные блокировки.** Изменённый
  `BRUTEFORCE_MAX_FAILURES` действует на новые окна; строка, у которой
  `locked_until` в будущем, остаётся заблокированной до конца окна.
- **`check` отделён от учёта.** `check` только читает блокировку и не инкрементит
  счётчик; рост — исключительно через `record_failure`. Не забыть вызвать
  `record_failure` на каждую неудачу проверки пароля.
- **Окно `check` → `record_failure` не атомарно.** Между чтением блокировки и
  учётом неудачи проходит проверка пароля, поэтому N одновременных попыток по
  одной паре успевают пройти `check` до первого `record_failure`. Это свойство
  модели, а не баг: счётчик всё равно инкрементится атомарно (upsert), и
  следующая волна попыток упрётся в блокировку. Жёсткий лимит на параллельные
  запросы — задача rate-limiter'а (`httpkit`), а не guard'а.
- **`record_success` обнуляет историю удалением строки.** Успешный вход стирает
  накопленные неудачи по `(subject, ip)` целиком — частичного декремента нет.
- **Unlock — пассивный, по времени.** Явного «разблокировать» нет: `check`
  возвращает `None`, как только `locked_until` оказывается в прошлом. Сама строка
  при этом остаётся до `cleanup` (или до `record_success`).
- **Скользящего окна нет.** Внутри окна неудачи не «истекают» сами: счётчик
  обнуляют `record_success`, истечение блокировки и `cleanup` по
  `cleanup_retention`.
- **Без `timerkit` таблица не чистится.** `cleanup_job` кладёт в `timerkit.Jobs`
  консьюмер; без запущенного `timerkit.Runner` джобы никто не выполняет. Рост
  таблицы attacker-driven: строка заводится на каждую новую пару `(subject, ip)`,
  включая несуществующие subject'ы.
- **Всё время — часы базы.** `locked_until`, `last_attempt` и их сравнение в
  `check`/`cleanup` считаются через `NOW()` Postgres; рассинхрон часов между
  инстансами сервиса на блокировки не влияет. Внутри `pgkit.Transactor.with_tx`
  `NOW()` — момент начала транзакции.
- **`cleanup` не трогает активные блокировки.** Удаляются только разблокированные
  строки старше `cleanup_retention`; заблокированная строка переживает `cleanup`
  независимо от возраста.
- **core + pgstore split.** Протоколы и `Config` — в `bruteforcekit`, SQL и
  dishka — в `pgstore`. Корневой пакет провайдера не экспортирует; единственный
  DI-вход — `BruteforcePgstoreProvider`, а `Config` приложение регистрирует явно.
