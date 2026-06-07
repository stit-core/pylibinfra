"""RBAC-примитивы: глобальный реестр роль → набор пермишенов и has().

Реестр глобальный (как roles в golibinfra): consumer заполняет его на старте
через register_role_permissions, проверки идут через has(). Lock защищает от
гонки регистрация/чтение при ленивой инициализации ролей в разных потоках.
"""

import threading

Permission = str
PermissionSet = frozenset[Permission]
RolePermissions = dict[str, PermissionSet]

_lock = threading.RLock()
_roles: RolePermissions = {}


def register_role_permissions(role: str, *perms: Permission) -> None:
    with _lock:
        _roles[role] = frozenset(perms)


def has(role: str, perm: Permission) -> bool:
    with _lock:
        return perm in _roles.get(role, frozenset())
