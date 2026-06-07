import pytest

from pylibinfra.authnkit import has, register_role_permissions


@pytest.fixture(autouse=True)
def _isolate_roles() -> None:
    # Реестр глобальный — изолируем тест, очищая его перед каждым прогоном.
    from pylibinfra.authnkit import permissions

    permissions._roles.clear()


def test_has() -> None:
    register_role_permissions("admin", "articles:read", "articles:write")

    assert has("admin", "articles:read") is True
    assert has("admin", "articles:write") is True
    assert has("admin", "unknown:perm") is False
    assert has("unknown-role", "articles:read") is False
    assert has("", "articles:read") is False
