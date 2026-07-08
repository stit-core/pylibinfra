"""Outbox — один независимый transactional-outbox: своя таблица (через repo),
конфиг, worker и timer-job. Приложение строит несколько Outbox'ов (по одному на
домен/таблицу) и регистрирует их job() в timerkit-группе, publisher() — где нужна
доменная публикация. Single-instance путь — OutboxProvider (module.py)."""

from __future__ import annotations

from pylibinfra.logkit import Logger
from pylibinfra.timerkit import PeriodicJob

from .bus import Bus, Publisher
from .config import Config
from .outbox import Repository
from .transport import Transport
from .worker import Worker


class Outbox:
    def __init__(
        self,
        name: str,
        repo: Repository,
        tr: Transport,
        cfg: Config,
        log: Logger | None = None,
    ) -> None:
        self._name = name
        self._bus = Bus(repo)
        self._worker = Worker(repo, tr, cfg, log)
        self._cfg = cfg

    def name(self) -> str:
        return self._name

    def publisher(self) -> Publisher:
        return self._bus

    def job(self) -> PeriodicJob:
        return PeriodicJob(
            name=f"outboxkit.publisher.{self._name}",
            interval=self._cfg.interval,
            handler=self._worker.run,
        )
