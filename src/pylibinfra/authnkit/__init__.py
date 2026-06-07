"""authnkit — building blocks для аутентификации.

Hasher (Argon2id), JWT sign/verify (HS256), TOTP-генератор (RFC 6238) и
RBAC-примитивы (Permission, RolePermissions, has). Конкретные auth-flow'ы
(login, refresh, MFA enrollment) — за consumer'ом, kit предоставляет только
криптографически-корректные кирпичики. Эмитит jwt_verifications_total{result},
password_{hash,verify}_duration_seconds, totp_verifications_total{result}.
"""

from .hasher import Argon2idHasher, Argon2idParams, Hasher
from .jwt import (
    HS256JWT,
    ErrTokenExpired,
    ErrTokenMalformed,
    ErrTokenSignature,
    JWTConfig,
    JWTParser,
    JWTSigner,
    TokenError,
)
from .module import AuthnProvider, new_argon2id_params, new_jwt_config
from .permissions import (
    Permission,
    PermissionSet,
    RolePermissions,
    has,
    register_role_permissions,
)
from .totp import TOTPGenerator

__all__ = [
    "HS256JWT",
    "Argon2idHasher",
    "Argon2idParams",
    "AuthnProvider",
    "ErrTokenExpired",
    "ErrTokenMalformed",
    "ErrTokenSignature",
    "Hasher",
    "JWTConfig",
    "JWTParser",
    "JWTSigner",
    "Permission",
    "PermissionSet",
    "RolePermissions",
    "TOTPGenerator",
    "TokenError",
    "has",
    "new_argon2id_params",
    "new_jwt_config",
    "register_role_permissions",
]
