from __future__ import annotations

import threading

import pytest

from pylibinfra.authnkit import Permission, Registry

READ: Permission = "articles:read"
WRITE: Permission = "articles:write"


@pytest.mark.parametrize(
    ("role", "perm", "expected"),
    [
        ("admin", READ, True),
        ("admin", WRITE, True),
        ("admin", "unknown:perm", False),
        ("unknown-role", READ, False),
        ("", READ, False),
    ],
)
def test_has(role: str, perm: Permission, expected: bool) -> None:
    registry = Registry()
    registry.register("admin", READ, WRITE)
    assert registry.has(role, perm) is expected


def test_register_replaces_the_whole_set() -> None:
    registry = Registry()
    registry.register("admin", READ, WRITE)
    registry.register("admin", READ)

    assert registry.has("admin", READ) is True
    assert registry.has("admin", WRITE) is False


def test_registries_do_not_share_state() -> None:
    registry = Registry()
    registry.register("admin", READ)
    assert Registry().has("admin", READ) is False


def test_concurrent_register_and_read() -> None:
    registry = Registry()

    def worker(index: int) -> None:
        if index % 2 == 0:
            registry.register("viewer", "x:read")
        else:
            registry.has("viewer", "x:read")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert registry.has("viewer", "x:read") is True
