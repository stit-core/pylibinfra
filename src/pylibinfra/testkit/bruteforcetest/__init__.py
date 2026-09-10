"""bruteforcetest — фейки `bruteforcekit.Guard` для тестов сценариев логина без Postgres.

Все четыре отвергают невалидный ключ так же, как pgstore: проводка, отдающая
guard'у пустой `ip`, падает в unit-тесте, а не на проде.
"""

from .guard import Attempt, Counting, Locked, Permissive, Recorder

__all__ = ["Attempt", "Counting", "Locked", "Permissive", "Recorder"]
