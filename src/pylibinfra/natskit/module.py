"""dishka-проводка инстанса шины: `Client`, `Conn`, `Bus` и `StreamBus` под общим lifecycle'ом."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from dishka import DEFAULT_COMPONENT, FromComponent, Provider, Scope, provide

from pylibinfra.logkit import Logger

from .bus import Bus
from .client import Client, new_client
from .config import DEFAULT_INSTANCE, Config
from .conn import Conn
from .errors import ConfigError
from .jetstream import StreamBus
from .subjects import valid_instance_name


class NatsProvider(Provider):
    """Provider инстанса шины; `Config` регистрирует консьюмер (asymmetry note).

    Вторая шина в том же приложении — второй `NatsProvider(name, component=name)` с
    конфигом `new_config_prefixed(name)` в том же компоненте; `Logger` берётся из
    дефолтного компонента.
    """

    scope = Scope.APP

    def __init__(self, name: str = DEFAULT_INSTANCE, *, component: str | None = None) -> None:
        if not name or not valid_instance_name(name):
            raise ConfigError(
                f"natskit: invalid config: instance {name!r}: "
                "allowed characters are letters, digits, '_', '-', '.'"
            )
        super().__init__(component=component)
        self.name = name

    @provide
    async def client(
        self, cfg: Config, log: Annotated[Logger, FromComponent(DEFAULT_COMPONENT)]
    ) -> AsyncIterator[Client]:
        """Инстанс шины на всё время жизни контейнера; на выходе — graceful `close`."""
        client = await new_client(self.name, cfg, log)
        try:
            yield client
        finally:
            await client.close()

    @provide
    def conn(self, client: Client) -> Conn:
        """Соединение инстанса (нужно для `probe`)."""
        return client.conn

    @provide
    def bus(self, client: Client) -> Bus:
        """Core-шина инстанса."""
        return client.bus

    @provide
    def stream_bus(self, client: Client) -> StreamBus:
        """JetStream-шина инстанса (заглушка при выключенном JetStream)."""
        return client.stream
