"""Фейковые реализации `bruteforcekit.Guard`: Permissive, Locked, Recorder, Counting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import ValidationError

from pylibinfra._internal import configkit
from pylibinfra.bruteforcekit import FULL_IPV6_PREFIX, Config, ConfigError, LockState
from pylibinfra.clockkit import Clock, Real

# Контракт ключа pgstore без агрегации IPv6: у фейков нет конфига, и адрес
# сравнивается как передан (кроме Counting, у которого конфиг свой).
_KEY_CFG = Config.model_validate({"ipv6_prefix": FULL_IPV6_PREFIX})


def _validate_key(subject: str, ip: str) -> None:
    _KEY_CFG.normalize_key(subject, ip)


@dataclass(frozen=True, slots=True)
class Attempt:
    """Одна зарегистрированная попытка: с каким ключом её позвал сервис."""

    subject: str
    ip: str


class Permissive:
    """Guard, который никогда не блокирует; ключ проверяется как в pgstore."""

    async def check(self, subject: str, ip: str) -> LockState | None:
        """Разрешает попытку; невалидный ключ — `InvalidKeyError`."""
        _validate_key(subject, ip)
        return None

    async def record_failure(self, subject: str, ip: str) -> LockState | None:
        """Ничего не делает и блокировку не выставляет; невалидный ключ — `InvalidKeyError`."""
        _validate_key(subject, ip)
        return None

    async def record_success(self, subject: str, ip: str) -> None:
        """Ничего не делает; невалидный ключ — `InvalidKeyError`."""
        _validate_key(subject, ip)


class Locked:
    """Guard, заблокированный до `until`; ключ проверяется как в pgstore."""

    def __init__(self, until: datetime) -> None:
        self.until = until

    async def check(self, subject: str, ip: str) -> LockState | None:
        """Отдаёт блокировку до `until`; невалидный ключ — `InvalidKeyError`."""
        _validate_key(subject, ip)
        return LockState(until=self.until)

    async def record_failure(self, subject: str, ip: str) -> LockState | None:
        """Отдаёт ту же блокировку, что и `check`; невалидный ключ — `InvalidKeyError`."""
        _validate_key(subject, ip)
        return LockState(until=self.until)

    async def record_success(self, subject: str, ip: str) -> None:
        """Ничего не делает; невалидный ключ — `InvalidKeyError`."""
        _validate_key(subject, ip)


class Recorder:
    """Guard, который пропускает всё и копит вызовы; невалидная пара в историю не попадает."""

    def __init__(self) -> None:
        self._failures: list[Attempt] = []
        self._successes: list[Attempt] = []

    async def check(self, subject: str, ip: str) -> LockState | None:
        """Разрешает попытку; невалидный ключ — `InvalidKeyError`."""
        _validate_key(subject, ip)
        return None

    async def record_failure(self, subject: str, ip: str) -> LockState | None:
        """Запоминает пару `(subject, ip)`; невалидный ключ — `InvalidKeyError`."""
        _validate_key(subject, ip)
        self._failures.append(Attempt(subject=subject, ip=ip))
        return None

    async def record_success(self, subject: str, ip: str) -> None:
        """Запоминает пару `(subject, ip)`; невалидный ключ — `InvalidKeyError`."""
        _validate_key(subject, ip)
        self._successes.append(Attempt(subject=subject, ip=ip))

    def failures(self) -> list[str]:
        """Subject'ы неудачных попыток в порядке записи."""
        return [a.subject for a in self._failures]

    def successes(self) -> list[str]:
        """Subject'ы успешных попыток в порядке записи."""
        return [a.subject for a in self._successes]

    def attempts(self) -> list[Attempt]:
        """Неудачные попытки вместе с ip: по subject не видно, какой адрес отдал сервис."""
        return list(self._failures)

    def success_attempts(self) -> list[Attempt]:
        """Успешные попытки с ip."""
        return list(self._successes)


class Counting:
    """Guard в памяти с политикой pgstore: N неудач → lock, истечение окна, сброс на успехе.

    Единственный фейк с конфигом: агрегирует IPv6 по `Config.ipv6_prefix`,
    реализует `bruteforcekit.Cleaner` и отвергает невалидный конфиг
    (`bruteforcekit.ConfigError`), как pgstore. Часы теста — `clockkit.Tickable`.
    """

    def __init__(self, cfg: Config, clock: Clock | None = None) -> None:
        try:
            self._cfg = Config.model_validate(cfg.model_dump())
        except ValidationError as exc:
            raise ConfigError(configkit.describe("bruteforcekit", exc)) from None
        self._clock = clock if clock is not None else Real()
        self._rows: dict[Attempt, _Row] = {}

    async def check(self, subject: str, ip: str) -> LockState | None:
        """Активная блокировка пары либо None; невалидный ключ — `InvalidKeyError`."""
        key = self._key(subject, ip)
        row = self._rows.get(key)
        if row is None or row.locked_until is None or row.locked_until <= self._clock.now():
            return None
        return LockState(until=row.locked_until, failed_count=row.failed_count)

    async def record_failure(self, subject: str, ip: str) -> LockState | None:
        """Инкрементит счётчик окна; `LockState`, если после попытки блокировка активна."""
        key = self._key(subject, ip)
        now = self._clock.now()
        row = self._rows.get(key)
        if row is None:
            row = _Row(failed_count=1)
            self._rows[key] = row
        elif row.locked_until is not None and row.locked_until <= now:
            row.failed_count = 1
            row.locked_until = None
        else:
            row.failed_count += 1
        if row.failed_count >= self._cfg.max_failures:
            row.locked_until = now + timedelta(seconds=self._cfg.lock_duration)

        if row.locked_until is None or row.locked_until <= now:
            return None
        return LockState(until=row.locked_until, failed_count=row.failed_count)

    async def record_success(self, subject: str, ip: str) -> None:
        """Стирает историю попыток пары; невалидный ключ — `InvalidKeyError`."""
        self._rows.pop(self._key(subject, ip), None)

    async def cleanup(self) -> None:
        """Удаляет разблокированные строки — как Cleaner в pgstore, но без возраста."""
        now = self._clock.now()
        for key, row in list(self._rows.items()):
            if row.locked_until is None or row.locked_until <= now:
                del self._rows[key]

    def _key(self, subject: str, ip: str) -> Attempt:
        norm_subject, norm_ip = self._cfg.normalize_key(subject, ip)
        return Attempt(subject=norm_subject, ip=norm_ip)


@dataclass(slots=True)
class _Row:
    failed_count: int = 0
    locked_until: datetime | None = None
