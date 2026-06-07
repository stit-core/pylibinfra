from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide

from pylibinfra.logkit import Logger

from .timerkit import PeriodicJob, Runner


# Provider не provide'ит list[PeriodicJob] (asymmetry note, см. cachekit/module.py) —
# caller собирает джобы из своих сервисов и регистрирует список сам.
class TimerkitProvider(Provider):
    scope = Scope.APP

    @provide
    async def runner(self, jobs: list[PeriodicJob], log: Logger) -> AsyncIterator[Runner]:
        r = Runner(jobs, log)
        r.start()
        yield r
        await r.stop()
