"""Circuit breaker на каждый upstream-хост.

Машина состояний: `closed` → `open` после `failure_threshold` подряд идущих
провалов; через `recovery_timeout` — `half-open` с одним пробным запросом
(успех → `closed`, провал → `open`). Отказ одного хоста не размыкает цепь
остальным; исход вызова кит сообщает цепи сам (`Guard`), поэтому отмена
вызывающего и отказ hook'а счётчик не двигают.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pylibinfra.clockkit import Clock, Real

from .config import BreakerConfig
from .errors import BreakerOpenError
from .metrics import Instruments


class State(StrEnum):
    """Состояние цепи; значения — label'ы `from`/`to` в метрике переходов."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class Guard:
    """Исход одного вызова для цепи хоста: успех, провал либо «не считать»."""

    def __init__(self, breaker: _HostBreaker | None) -> None:
        self._breaker = breaker

    def success(self) -> None:
        """Успешный вызов: счётчик подряд идущих провалов сбрасывается."""
        if self._breaker is not None:
            self._breaker.record_success()

    def failure(self) -> None:
        """Провал upstream'а: транспорт, таймаут попытки или 502/503/504 после ретраев."""
        if self._breaker is not None:
            self._breaker.record_failure()

    def skip(self) -> None:
        """Исход не про здоровье хоста (отмена вызывающего, отказ hook'а): цепь не трогаем."""
        if self._breaker is not None:
            self._breaker.release()


class Breakers:
    """Набор цепей по хостам с эмиссией `http_client_breaker_*`."""

    def __init__(self, cfg: BreakerConfig, instr: Instruments, clock: Clock | None = None) -> None:
        self._cfg = cfg
        self._instr = instr
        self._clock = clock if clock is not None else Real()
        self._hosts: dict[str, _HostBreaker] = {}

    def guard(self, host: str) -> Guard:
        """Пропускает вызов в цепь хоста; разомкнутая — `BreakerOpenError` без запроса."""
        if self._cfg.failure_threshold == 0:
            return Guard(None)
        breaker = self._hosts.get(host)
        if breaker is None:
            breaker = _HostBreaker(host, self._cfg, self._instr, self._clock)
            self._hosts[host] = breaker
        breaker.acquire()
        return Guard(breaker)

    def state(self, host: str) -> State:
        """Текущее состояние цепи хоста; хост без вызовов — `closed`."""
        breaker = self._hosts.get(host)
        return breaker.state if breaker is not None else State.CLOSED


class _HostBreaker:
    """Цепь одного хоста; живёт в одном event-loop, синхронизация не нужна."""

    def __init__(self, host: str, cfg: BreakerConfig, instr: Instruments, clock: Clock) -> None:
        self._host = host
        self._threshold = cfg.failure_threshold
        self._recovery = cfg.recovery_timeout
        self._instr = instr
        self._clock = clock
        self._state = State.CLOSED
        self._failures = 0
        self._opened_at: datetime | None = None
        # В half-open в upstream уходит ровно один пробный запрос; остальные
        # получают BreakerOpenError, пока проба не вернулась.
        self._probing = False

    @property
    def state(self) -> State:
        return self._state

    def acquire(self) -> None:
        """Впускает вызов; на разомкнутой цепи — `BreakerOpenError`."""
        if self._state is State.OPEN:
            if self._opened_at is None or self._clock.since(self._opened_at) < self._recovery:
                raise BreakerOpenError(f"httpclient: circuit breaker open: {self._host}")
            self._switch(State.HALF_OPEN)
        if self._state is State.HALF_OPEN:
            if self._probing:
                raise BreakerOpenError(f"httpclient: circuit breaker half-open: {self._host}")
            self._probing = True

    def release(self) -> None:
        """Освобождает пробу, не двигая счётчик: исход не про здоровье хоста."""
        self._probing = False

    def record_success(self) -> None:
        """Успех: в half-open замыкает цепь, в closed сбрасывает счётчик провалов."""
        self._probing = False
        self._failures = 0
        if self._state is State.HALF_OPEN:
            self._switch(State.CLOSED)

    def record_failure(self) -> None:
        """Провал: в half-open сразу размыкает цепь, в closed копит до порога."""
        self._probing = False
        if self._state is State.HALF_OPEN:
            self._failures = 0
            self._open()
            return
        self._failures += 1
        if self._failures >= self._threshold:
            self._open()

    def _open(self) -> None:
        self._opened_at = self._clock.now()
        self._switch(State.OPEN)

    def _switch(self, to: State) -> None:
        previous, self._state = self._state, to
        self._instr.breaker_transitions.add(
            1, {"host": self._host, "from": str(previous), "to": str(to)}
        )
        if to is State.OPEN:
            self._instr.breaker_open.add(1, {"host": self._host})
        elif previous is State.OPEN:
            self._instr.breaker_open.add(-1, {"host": self._host})
