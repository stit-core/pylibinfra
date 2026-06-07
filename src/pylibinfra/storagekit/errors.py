"""Стабильные ошибки storagekit. Имена зеркалят golibinfra-sentinel'ы; в Python
это классы исключений — потребитель ловит по типу (`except ErrNotFound`)."""


class StorageError(Exception):
    """Базовый класс ошибок kit'а — общий `except` для всего домена."""


class ErrUploadFailed(StorageError):  # noqa: N818
    pass


class ErrNotFound(StorageError):  # noqa: N818
    pass
