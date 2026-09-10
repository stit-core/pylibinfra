"""RBAC: экземплярный реестр «роль → набор прав»."""

from __future__ import annotations

import threading

Permission = str
"""Право в RBAC-реестре — строковый идентификатор вида `users:write`."""


class Registry:
    """Реестр ролей: наполняется на старте приложения, читается на каждом запросе.

    Неизвестная роль или право → `False` (fail-closed). Экземплярный: общего
    состояния между контейнерами и тестами нет.
    """

    def __init__(self) -> None:
        # Реестр наполняется на старте, а читается из нескольких потоков
        # (sync-хендлеры FastAPI) — замена набора роли должна быть атомарной.
        self._lock = threading.RLock()
        self._roles: dict[str, frozenset[Permission]] = {}

    def register(self, role: str, *perms: Permission) -> None:
        """Полностью заменяет набор прав роли."""
        with self._lock:
            self._roles[role] = frozenset(perms)

    def has(self, role: str, perm: Permission) -> bool:
        """Есть ли право `perm` у роли `role`; неизвестная роль или право → `False`."""
        with self._lock:
            return perm in self._roles.get(role, frozenset())
