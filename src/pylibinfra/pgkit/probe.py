"""DBProbe — health-probe: ping через SELECT 1."""

from .db import DB


def db_probe(db: DB) -> "DBProbe":
    return DBProbe(db)


class DBProbe:
    def __init__(self, db: DB) -> None:
        self._db = db

    async def __call__(self) -> None:
        await self._db.fetchval("SELECT 1")
