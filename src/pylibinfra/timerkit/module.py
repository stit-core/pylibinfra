"""TimerkitProvider — `Runner` поверх кортежа `Jobs`, собранного App-Provider'ом консьюмера."""

from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide

from pylibinfra.clockkit import Clock
from pylibinfra.logkit import Logger

from .timerkit import Jobs, Runner


class TimerkitProvider(Provider):
    """Provider не собирает `Jobs` сам — их регистрирует консьюмер, перекрывая пустой дефолт.

    `Logger` и `Clock` берутся из контейнера (`LogkitProvider`, `ClockProvider`).
    """

    scope = Scope.APP

    @provide
    def default_jobs(self) -> Jobs:
        """Пустой набор job'ов; консьюмер регистрирует свой Provider ПОСЛЕ этого."""
        return ()

    @provide
    async def runner(self, jobs: Jobs, log: Logger, clock: Clock) -> AsyncIterator[Runner]:
        """Runner живёт от получения из контейнера до его закрытия."""
        runner = Runner(jobs, log, clock=clock)
        runner.start()
        yield runner
        await runner.stop()
