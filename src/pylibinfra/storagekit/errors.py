"""Ошибки storagekit: база `StorageError` и подклассы по операциям порта."""

from __future__ import annotations

from pylibinfra._internal import configkit


class StorageError(Exception):
    """База ошибок кита — общий `except` для всего домена."""


class ConfigError(configkit.ConfigError, StorageError):
    """Невалидный `Config`: формат endpoint'а, пустой бакет, part size вне границ."""


class UploadFailedError(StorageError):
    """`put` не сохранил объект."""


class SizeMismatchError(UploadFailedError):
    """Тело короче или длиннее заявленного `size`; объект в хранилище не остаётся.

    Подкласс `UploadFailedError` — широкий `except UploadFailedError` ловит и
    расхождение размера (аналог двойной обёртки Go-sentinel'ов).
    """


class GetFailedError(StorageError):
    """`get` не открыл объект."""


class StatFailedError(StorageError):
    """`stat` не получил метаданные объекта."""


class DeleteFailedError(StorageError):
    """`delete` не удалил объект (пустой ключ или отказ хранилища)."""


class PresignFailedError(StorageError):
    """Ссылка не выдана: пустой ключ, ttl вне диапазона или отказ подписи."""


class NotFoundError(GetFailedError, StatFailedError):
    """Объекта с таким ключом нет: поднимают `get` и `stat`, но не `delete` (он идемпотентен).

    Подкласс обеих ошибок операции, поэтому ловится и точечно
    (`except NotFoundError`), и широко (`except GetFailedError`) — как
    `errors.Is(err, ErrNotFound)` и `errors.Is(err, ErrGetFailed)` в Go.
    """
