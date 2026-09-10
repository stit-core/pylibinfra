"""Outbox — один независимый transactional-outbox: своя таблица, конфиг, `Bus`, `Worker` и job'ы."""

from __future__ import annotations

from pylibinfra.logkit import Logger
from pylibinfra.pgkit import Transactor
from pylibinfra.timerkit import Job

from .bus import Bus, Publisher
from .config import Config
from .errors import NotSupportedError
from .metrics import (
    BacklogRegistration,
    BacklogReporter,
    OutboxInstruments,
    register_backlog_gauges,
)
from .outbox import DeadReviver, Maintainer, Repository, ReviveFilter
from .transport import Transport
from .worker import COMMIT_GRACE, Worker


class Outbox:
    """Именованный инстанс: `Bus` + `Worker` + job'ы, все сигналы под label `instance`."""

    def __init__(
        self,
        name: str,
        repo: Repository,
        tr: Transport,
        tx: Transactor,
        cfg: Config,
        log: Logger | None = None,
    ) -> None:
        instr = OutboxInstruments(name)
        self.name = name
        self._repo = repo
        self._cfg = cfg
        self._instr = instr
        self._bus = Bus(name, repo, instr)
        self._worker = Worker(name, repo, tr, tx, cfg, log, instr)

    def bus(self) -> Bus:
        """Insert-сторона инстанса (конкретный тип)."""
        return self._bus

    def publisher(self) -> Publisher:
        """Insert-сторона инстанса для доменного кода."""
        return self._bus

    def worker(self) -> Worker:
        """Drain-сторона инстанса (обычно нужна только через `job()`)."""
        return self._worker

    def job(self) -> Job:
        """Периодический drain инстанса для кортежа `timerkit.Jobs` консьюмера.

        Бюджет запуска — дедлайн тика плюс запас на фазу mark/commit: она идёт
        мимо отмены и должна успеть закоммитить подтверждённые публикации.
        """
        return Job(
            name=f"outboxkit.publisher.{self.name}",
            interval=self._cfg.interval,
            run=self._worker.run,
            timeout=self._cfg.tick_timeout + COMMIT_GRACE,
        )

    def jobs(self) -> list[Job]:
        """Drain инстанса плюс job'ы обслуживания хранилища, если оно их отдаёт."""
        jobs = [self.job()]
        if isinstance(self._repo, Maintainer):
            jobs.extend(self._repo.maintenance_jobs())
        return jobs

    def register_backlog_gauges(self) -> BacklogRegistration | None:
        """Подключает `outbox_lag_seconds`/`outbox_pending_count`; `None` — store их не отдаёт."""
        if not isinstance(self._repo, BacklogReporter):
            return None
        return register_backlog_gauges(self.name, self._repo)

    async def revive_dead(self, filter: ReviveFilter | None = None) -> int:
        """Возвращает dead-строки в очередь и считает их в `outbox_revived_total`."""
        if not isinstance(self._repo, DeadReviver):
            raise NotSupportedError("outboxkit: revive_dead is not supported by repository")
        revived = await self._repo.revive_dead(filter or ReviveFilter())
        if revived > 0:
            self._instr.revived_total.add(revived, self._instr.attrs)
        return revived


def new_outbox(
    name: str,
    repo: Repository,
    tr: Transport,
    tx: Transactor,
    cfg: Config,
    log: Logger | None = None,
) -> Outbox:
    """Собирает именованный outbox поверх готовых repo/transport/transactor."""
    return Outbox(name, repo, tr, tx, cfg, log)
