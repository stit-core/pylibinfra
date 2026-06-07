from dishka import Provider, Scope, provide

from .flag import Config, EnvStore, Store


# Provider не provide'ит Config (asymmetry note, см. pgkit/module.py) — caller
# регистрирует Config сам. Дешёвый mode: fx-аналог через new_config() ниже.
class FlagkitProvider(Provider):
    scope = Scope.APP

    @provide(provides=Store)
    def store(self, cfg: Config) -> EnvStore:
        return EnvStore(cfg)


def new_config() -> Config:
    return Config()
