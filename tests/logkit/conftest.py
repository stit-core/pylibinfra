from __future__ import annotations

import io
import logging
from collections.abc import Iterator

import pytest

from pylibinfra.logkit import attrs


@pytest.fixture(autouse=True)
def _isolate_globals() -> Iterator[None]:
    """Реестр ctx-полей и root-logging глобальны на процесс — возвращаем их как было."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    attrs._reset()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


@pytest.fixture
def buf() -> io.StringIO:
    return io.StringIO()
