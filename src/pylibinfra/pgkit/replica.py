"""ReplicaConfig + ReadWriteDB: чтения идут на реплику, записи — на мастер.

Реплика опциональна: при пустом `PG_REPLICA_DSN` обе стороны указывают на
мастер, и read/write-split-код работает в обеих топологиях.
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, SecretStr, model_validator

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .db import DB
from .errors import ConfigError
from .pool import PoolConfig


class ReplicaConfig(KitSettings):
    """Опциональная read-replica; пустой `PG_REPLICA_DSN` означает «реплики нет»."""

    model_config = configkit.settings_config()

    replica_dsn: SecretStr = Field(default=SecretStr(""), validation_alias="PG_REPLICA_DSN")
    max_conns: int = Field(default=10, validation_alias="PG_REPLICA_MAX_CONNS")

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Проверяет инварианты; текст ошибки называет ENV-переменную, а не поле."""
        if self.max_conns < 0:
            raise ValueError(f"PG_REPLICA_MAX_CONNS must be >= 0, got {self.max_conns}")
        return self

    def has_replica(self) -> bool:
        """True, если `PG_REPLICA_DSN` задан и реплику надо поднимать."""
        return bool(self.replica_dsn.get_secret_value())

    def pool_config(self, base: PoolConfig) -> PoolConfig:
        """Конфиг пула реплики: DSN и лимит свои, остальное наследуется от primary.

        Кросс-инвариант `PG_REPLICA_MAX_CONNS >= PG_MIN_CONNS` назван именами
        переменных реплики: правит оператор именно их.
        """
        if self.max_conns > 0 and base.min_conns > self.max_conns:
            raise ConfigError(
                "pgkit: invalid config: "
                f"PG_REPLICA_MAX_CONNS ({self.max_conns}) must be >= "
                f"PG_MIN_CONNS ({base.min_conns})"
            )
        return base.model_copy(update={"dsn": self.replica_dsn, "max_conns": self.max_conns})


def new_replica_config() -> ReplicaConfig:
    """Читает `ReplicaConfig` из ENV; нарушение инварианта — `pgkit.ConfigError`."""
    return configkit.build(ReplicaConfig, "pgkit", ConfigError)


class ReadWriteDB:
    """Пара пулов read/write; без реплики обе стороны указывают на мастер."""

    def __init__(self, master: DB, replica: DB | None) -> None:
        self._master = master
        self._replica = replica if replica is not None else master

    def read(self) -> DB:
        """Реплика (мастер, если реплики нет) — для запросов, терпящих лаг репликации."""
        return self._replica

    def write(self) -> DB:
        """Мастер — для записей и чтений, которым нужен read-after-write."""
        return self._master

    def has_replica(self) -> bool:
        """True, если поднят отдельный пул реплики."""
        return self._replica is not self._master
