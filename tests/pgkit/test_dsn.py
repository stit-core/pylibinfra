"""Редактирование DSN: пароль не должен попадать в текст ошибки кита."""

from __future__ import annotations

from pathlib import Path

import pytest

from pylibinfra.pgkit import ConnectError, MigrateError, PoolConfig, Source, redact_dsn
from pylibinfra.pgkit import migrate as migrate_module
from pylibinfra.pgkit.dsn import safe_error
from pylibinfra.pgkit.pool import new_pool

_SECRET = "s3cr3t-PASSWORD"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://app:s3cr3t@db:5432/app", "postgresql://app:xxxxx@db:5432/app"),
        (
            "postgresql://app:p%40ss@db/app?sslmode=require",
            "postgresql://app:xxxxx@db/app?sslmode=require",
        ),
        ("postgresql://db:5432/app", "postgresql://db:5432/app"),
        ("postgresql://app@db/app", "postgresql://app@db/app"),
        (
            "host=db user=app password=s3cr3t dbname=app",
            "host=db user=app password=xxxxx dbname=app",
        ),
        ("host=db password='p a s s' dbname=app", "host=db password=xxxxx dbname=app"),
        # Испорченная оператором строка — как раз та, что уезжает в текст ошибки драйвера.
        ("app:s3cr3t@db/app", "app:xxxxx@db/app"),
        ("//app:s3cr3t@db/app", "//app:xxxxx@db/app"),
        ("", ""),
    ],
)
def test_redact_dsn(raw: str, expected: str) -> None:
    assert redact_dsn(raw) == expected


def test_safe_error_strips_dsn_and_password() -> None:
    dsn = f"postgresql://app:{_SECRET}@db/app"
    text = safe_error(dsn, ValueError(f"No scheme specified in connection URI '{dsn}'"))
    assert _SECRET not in text
    assert "postgresql://app:xxxxx@db/app" in text


def test_safe_error_strips_bare_password() -> None:
    dsn = f"postgresql://app:{_SECRET}@db/app"
    assert _SECRET not in safe_error(dsn, ValueError(f"password {_SECRET} rejected"))


def test_safe_error_strips_keyword_password() -> None:
    dsn = f"host=db password={_SECRET} dbname=app"
    text = safe_error(dsn, ValueError(f'invalid dsn: missing "=" after "{_SECRET}"'))
    assert _SECRET not in text


def test_safe_error_decodes_percent_encoded_password() -> None:
    dsn = "postgresql://app:p%40ss@db/app"
    assert "p@ss" not in safe_error(dsn, ValueError("password p@ss rejected"))


def test_safe_error_without_dsn_keeps_text() -> None:
    assert safe_error("", ValueError("boom")) == "boom"


def _source(tmp_path: Path) -> Source:
    directory = tmp_path / "mig"
    directory.mkdir()
    (directory / "0001_x.sql").write_text("SELECT 1;")
    return Source(name="leaky", path=str(directory))


def test_migrate_up_does_not_leak_password(tmp_path: Path) -> None:
    # Без схемы yoyo кладёт всю строку подключения в текст BadConnectionURI.
    dsn = f"//app:{_SECRET}@127.0.0.1:1/app"
    with pytest.raises(MigrateError) as exc_info:
        migrate_module.migrate_up(dsn, _source(tmp_path))
    assert _SECRET not in str(exc_info.value)


def test_migrate_up_all_does_not_leak_password(tmp_path: Path) -> None:
    # Неразбираемая keyword-строка: psycopg2 цитирует её в invalid dsn.
    dsn = f"app:{_SECRET}@127.0.0.1:1/app"
    with pytest.raises(MigrateError) as exc_info:
        migrate_module.migrate_up_all(dsn, _source(tmp_path), timeout=2.0)
    assert _SECRET not in str(exc_info.value)


async def test_new_pool_does_not_leak_password() -> None:
    cfg = PoolConfig.model_validate(
        {"dsn": f"postgresql://app:{_SECRET}@127.0.0.1:1/app", "connect_timeout": 2.0}
    )
    with pytest.raises(ConnectError) as exc_info:
        await new_pool(cfg)
    assert _SECRET not in str(exc_info.value)
