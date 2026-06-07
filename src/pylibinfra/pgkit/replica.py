"""ReplicaConfig + ReadWriteDB: маршрутизация чтений на реплику, записей на
мастер. Без реплики read() == write() (мастер)."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .db import DB


class ReplicaConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    replica_dsn: str = Field(default="", validation_alias="PG_REPLICA_DSN")
    max_conns: int = Field(default=10, validation_alias="PG_REPLICA_MAX_CONNS")


class ReadWriteDB:
    def __init__(self, master: DB, replica: DB | None) -> None:
        self._master = master
        self._replica = replica if replica is not None else master

    def read(self) -> DB:
        return self._replica

    def write(self) -> DB:
        return self._master
