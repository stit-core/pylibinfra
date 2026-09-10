"""AuthnProvider, TOTPProvider и ENV-фабрики конфигов кита."""

from __future__ import annotations

from dishka import AnyOf, Provider, Scope, provide

from pylibinfra._internal import configkit
from pylibinfra.clockkit import Clock

from .errors import ConfigError
from .hasher import Argon2idHasher, Argon2idParams, Hasher
from .jwt import HS256JWT, JWTConfig, JWTParser, JWTSigner
from .permissions import Registry
from .totp import TOTP, TOTPConfig
from .usedcode import UsedCodeStore

OptionalUsedCodeStore = UsedCodeStore | None


class AuthnProvider(Provider):
    """Хэшер, JWT и RBAC-реестр из конфигов, зарегистрированных консьюмером.

    Требует в контейнере `Argon2idParams`, `JWTConfig` (asymmetry note: сами
    конфиги provider не отдаёт, дешёвый mode — `new_*`-фабрики) и `Clock`
    (`clockkit.ClockProvider`). Второй фактор — отдельный `TOTPProvider`.
    """

    scope = Scope.APP

    @provide(provides=AnyOf[Argon2idHasher, Hasher])
    def hasher(self, params: Argon2idParams) -> Argon2idHasher:
        """Argon2id-хэшер; параметры вне окна валят сборку графа (`ConfigError`)."""
        return Argon2idHasher(params)

    @provide(provides=AnyOf[HS256JWT, JWTSigner, JWTParser])
    def jwt(self, cfg: JWTConfig, clock: Clock) -> HS256JWT:
        """Один HS256-инстанс под оба порта; `iss`/`aud` задаются прямым конструктором."""
        return HS256JWT(cfg, clock=clock)

    @provide
    def registry(self) -> Registry:
        """Пустой RBAC-реестр; роли регистрирует приложение на старте."""
        return Registry()


class TOTPProvider(Provider):
    """Второй фактор: требует в контейнере `TOTPConfig` и `Clock`.

    Отдельный provider, потому что dishka проверяет граф целиком на сборке
    контейнера: в `AuthnProvider` он требовал бы `TOTP_ISSUER` и от приложений
    без 2FA. `UsedCodeStore` опционален — дефолт `None` (защиты от replay нет),
    консьюмер перекрывает его своим провайдером, зарегистрированным ПОСЛЕ.
    """

    scope = Scope.APP

    @provide
    def default_used_codes(self) -> OptionalUsedCodeStore:
        """Дефолт опциональной зависимости: replay-защиты нет."""
        return None

    @provide
    def totp(self, cfg: TOTPConfig, clock: Clock, used_codes: OptionalUsedCodeStore) -> TOTP:
        """Генератор и валидатор кодов второго фактора."""
        return TOTP(cfg, clock=clock, used_codes=used_codes)


def new_argon2id_params() -> Argon2idParams:
    """Читает `ARGON2_*` из ENV; вне окна → `ConfigError`."""
    return configkit.build(Argon2idParams, "authnkit", ConfigError)


def new_jwt_config() -> JWTConfig:
    """Читает `JWT_*` из ENV; короткий секрет или неположительный TTL → `ConfigError`."""
    return configkit.build(JWTConfig, "authnkit", ConfigError)


def new_totp_config() -> TOTPConfig:
    """Читает `TOTP_*` из ENV; пустой issuer или skew вне `0..2` → `ConfigError`."""
    return configkit.build(TOTPConfig, "authnkit", ConfigError)
