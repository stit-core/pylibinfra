"""FlagkitProvider — `Store` поверх `Config` в dishka-контейнере."""

from dishka import Provider, Scope, provide

from .flag import Config, EnvStore, Store


class FlagkitProvider(Provider):
    """Provider не provide'ит `Config` (asymmetry note) — его регистрирует консьюмер."""

    scope = Scope.APP

    @provide(provides=Store)
    def store(self, cfg: Config) -> EnvStore:
        """`Store` со снимком флагов; невалидная `FEATURE_FLAGS` — ConfigError при сборке."""
        return EnvStore(cfg)
