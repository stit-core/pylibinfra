"""Store — `bruteforcekit.Guard` и `bruteforcekit.Cleaner` на таблице `bruteforce_attempts`.

Всё время — часы базы (`NOW()`): рассинхрон часов между инстансами сервиса на
блокировки не влияет.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from pylibinfra._internal import configkit
from pylibinfra.bruteforcekit.config import Config, ConfigError
from pylibinfra.bruteforcekit.guard import LockState
from pylibinfra.pgkit import DB

from .metrics import RESULT_ALLOWED, RESULT_LOCKED, Metrics

# Батч: на разросшейся таблице один безлимитный DELETE не укладывается в
# PG_STATEMENT_TIMEOUT и отменяется, не удалив ничего.
CLEANUP_BATCH_SIZE = 5000

_CHECK_SQL = """
SELECT failed_count, locked_until
FROM bruteforce_attempts
WHERE subject = $1 AND ip = $2 AND locked_until > NOW()
"""

# Истёкшая блокировка начинает новое окно (failed_count = 1), активная переживает
# поднятие max_failures. prev — состояние строки до апсерта: по нему считается
# переход в блокировку.
_RECORD_FAILURE_SQL = """
WITH prev AS (
    SELECT locked_until FROM bruteforce_attempts WHERE subject = $1 AND ip = $2
)
INSERT INTO bruteforce_attempts (subject, ip, failed_count, last_attempt, locked_until)
VALUES ($1, $2, 1, NOW(), CASE WHEN 1 >= $3 THEN NOW() + make_interval(secs => $4) END)
ON CONFLICT (subject, ip) DO UPDATE
SET failed_count = CASE
        WHEN bruteforce_attempts.locked_until <= NOW() THEN 1
        ELSE bruteforce_attempts.failed_count + 1
    END,
    last_attempt = NOW(),
    locked_until = CASE
        WHEN CASE
            WHEN bruteforce_attempts.locked_until <= NOW() THEN 1
            ELSE bruteforce_attempts.failed_count + 1
        END >= $3 THEN NOW() + make_interval(secs => $4)
        WHEN bruteforce_attempts.locked_until > NOW() THEN bruteforce_attempts.locked_until
    END
RETURNING failed_count,
          CASE WHEN locked_until > NOW() THEN locked_until END AS active_until,
          COALESCE((SELECT p.locked_until > NOW() FROM prev p), false) AS was_locked
"""

_RECORD_SUCCESS_SQL = "DELETE FROM bruteforce_attempts WHERE subject = $1 AND ip = $2"

_CLEANUP_SQL = """
DELETE FROM bruteforce_attempts
WHERE ctid IN (
    SELECT ctid FROM bruteforce_attempts
    WHERE (locked_until IS NULL OR locked_until < NOW())
      AND last_attempt < NOW() - make_interval(secs => $1)
    LIMIT $2
)
"""


class BruteforceError(RuntimeError):
    """Запрос guard'а к базе не прошёл."""


class Store:
    """Guard и Cleaner на одной таблице с составным ключом `(subject, ip)`."""

    def __init__(self, db: DB, cfg: Config) -> None:
        try:
            Config.model_validate(cfg.model_dump())
        except ValidationError as exc:
            raise ConfigError(configkit.describe("bruteforcekit", exc)) from None
        self._db = db
        self._cfg = cfg
        self._metrics = Metrics()

    async def check(self, subject: str, ip: str) -> LockState | None:
        """Активная блокировка пары либо None; невалидный ключ — `InvalidKeyError` до запроса."""
        subject, ip = self._cfg.normalize_key(subject, ip)
        try:
            row = await self._db.fetchrow(_CHECK_SQL, subject, ip)
        except Exception as exc:
            raise BruteforceError(f"bruteforcekit: check: {exc}") from exc
        if row is None:
            self._metrics.check(RESULT_ALLOWED)
            return None
        self._metrics.check(RESULT_LOCKED)
        return LockState(until=row["locked_until"], failed_count=row["failed_count"])

    async def record_failure(self, subject: str, ip: str) -> LockState | None:
        """Учитывает неудачу; `LockState`, если после неё блокировка активна, иначе None.

        Апсерт без строки в `RETURNING` — ошибка, а не «попыток ещё есть»: guard,
        молча не посчитавший неудачу, не заблокирует пару никогда.
        """
        subject, ip = self._cfg.normalize_key(subject, ip)
        try:
            row = await self._db.fetchrow(
                _RECORD_FAILURE_SQL,
                subject,
                ip,
                self._cfg.max_failures,
                self._cfg.lock_duration,
            )
        except Exception as exc:
            raise BruteforceError(f"bruteforcekit: record failure: {exc}") from exc
        if row is None:
            raise BruteforceError("bruteforcekit: record failure: upsert returned no row")

        until: datetime | None = row["active_until"]
        # Считается переход «активной блокировки не было → есть», а не равенство
        # счётчика порогу: понижение max_failures между деплоями блокирует строку
        # со счётчиком выше порога, и равенство такой lock не заметило бы.
        if until is not None and not row["was_locked"]:
            self._metrics.lock()
        if until is None:
            return None
        return LockState(until=until, failed_count=row["failed_count"])

    async def record_success(self, subject: str, ip: str) -> None:
        """Удаляет строку пары — успех обнуляет историю попыток."""
        subject, ip = self._cfg.normalize_key(subject, ip)
        try:
            await self._db.execute(_RECORD_SUCCESS_SQL, subject, ip)
        except Exception as exc:
            raise BruteforceError(f"bruteforcekit: record success: {exc}") from exc

    async def cleanup(self) -> None:
        """Удаляет разблокированные строки старше retention батчами; активные не трогает."""
        while True:
            try:
                tag = await self._db.execute(
                    _CLEANUP_SQL, self._cfg.cleanup_retention, CLEANUP_BATCH_SIZE
                )
            except Exception as exc:
                raise BruteforceError(f"bruteforcekit: cleanup: {exc}") from exc
            deleted = _rows_affected(tag)
            self._metrics.cleanup_deleted(deleted)
            if deleted < CLEANUP_BATCH_SIZE:
                return


def _rows_affected(tag: str) -> int:
    """Число строк из command tag (`DELETE 42`); нераспознанный tag — 0."""
    parts = tag.rsplit(" ", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return 0
    return int(parts[1])
