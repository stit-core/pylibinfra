"""Health-пробы кита: `SELECT 1` на пуле primary и на пуле реплики."""

from __future__ import annotations

from pylibinfra import healthkit

from .db import DB
from .errors import PgkitError
from .replica import ReadWriteDB


def probe(db: DB) -> healthkit.Probe:
    """Проба `pgkit`: `SELECT 1` на пуле primary; ошибка префиксуется именем кита."""

    async def check() -> None:
        try:
            await db.fetchval("SELECT 1")
        except Exception as exc:
            raise PgkitError(f"pgkit: ping: {exc}") from exc

    return healthkit.new("pgkit", check)


def replica_probe(rw: ReadWriteDB) -> healthkit.Probe:
    """Проба `pgkit/replica`: без реплики проходит без запроса — мастер покрыт `probe`."""

    async def check() -> None:
        if not rw.has_replica():
            return
        try:
            await rw.read().fetchval("SELECT 1")
        except Exception as exc:
            raise PgkitError(f"pgkit: replica ping: {exc}") from exc

    return healthkit.new("pgkit/replica", check)
