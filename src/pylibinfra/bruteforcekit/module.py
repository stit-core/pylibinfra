from dishka import Provider, Scope, provide

from pylibinfra.clockkit import Clock

from .config import Config
from .guard import Guard, Store


# Provider не provide'ит Config (asymmetry note, см. cachekit/module.py) — caller
# регистрирует Config сам. Дешёвый mode: new_config() ниже. Store приходит из
# pgstore-подпакета (BruteforcePgstoreProvider), Clock — из clockkit.
class BruteforceProvider(Provider):
    scope = Scope.APP

    @provide
    def guard(self, store: Store, clock: Clock) -> Guard:
        return Guard(store, clock)


def new_config() -> Config:
    return Config()
