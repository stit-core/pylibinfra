from datetime import datetime
from typing import cast

from pylibinfra.bruteforcekit.config import Config
from pylibinfra.pgkit import DB

_READ_SQL = """
SELECT locked_until
FROM bruteforce_attempts
WHERE subject = $1 AND ip = $2
"""

_RECORD_FAILURE_SQL = """
INSERT INTO bruteforce_attempts (subject, ip, failed_count, last_attempt)
VALUES ($1, $2, 1, NOW())
ON CONFLICT (subject, ip) DO UPDATE
SET failed_count = bruteforce_attempts.failed_count + 1,
    last_attempt = NOW(),
    locked_until = CASE
        WHEN bruteforce_attempts.failed_count + 1 >= $3
        THEN NOW() + make_interval(secs => $4)
        ELSE NULL
    END
"""

_RESET_SQL = "DELETE FROM bruteforce_attempts WHERE subject = $1 AND ip = $2"

_CLEANUP_SQL = """
DELETE FROM bruteforce_attempts
WHERE (locked_until IS NULL OR locked_until < NOW())
  AND last_attempt < NOW() - make_interval(secs => $1)
"""


class PgStore:
    def __init__(self, db: DB, cfg: Config) -> None:
        self._db = db
        self._cfg = cfg

    async def read(self, subject: str, ip: str) -> datetime | None:
        # fetchval даёт None и при отсутствии строки, и при NULL locked_until —
        # оба случая значат «не заблокирован», как в Go (IsNoRows / nil).
        return cast("datetime | None", await self._db.fetchval(_READ_SQL, subject, ip))

    async def record_failure(self, subject: str, ip: str) -> None:
        lock_secs = int(self._cfg.lock_duration)
        await self._db.execute(_RECORD_FAILURE_SQL, subject, ip, self._cfg.max_failures, lock_secs)

    async def reset(self, subject: str, ip: str) -> None:
        await self._db.execute(_RESET_SQL, subject, ip)

    async def cleanup(self) -> None:
        retention_secs = int(self._cfg.cleanup_retention)
        await self._db.execute(_CLEANUP_SQL, retention_secs)
