import threading
from pathlib import Path

import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.pgkit import (
    AppSourceName,
    Migrated,
    MigrateError,
    MigrateRunnerConfig,
    MigrationSources,
    PgMigrateProvider,
    PoolConfig,
    Source,
    migrate_up_all,
    startup_sources,
)
from pylibinfra.pgkit.migrate import _version_of, advisory_lock_key


def _dir_with_migration(tmp_path: Path, name: str = "0001_init") -> str:
    d = tmp_path / "migrations"
    d.mkdir(exist_ok=True)
    (d / f"{name}.sql").write_text("SELECT 1;")
    return str(d)


def test_version_table_name() -> None:
    assert Source(name="auditkit", path="/x").version_table() == "schema_migrations_auditkit"
    assert Source(name=AppSourceName, path="/x").version_table() == "schema_migrations_app"


@pytest.mark.parametrize("name", ["", "Audit", "1kit", "kit-name", "a" * 46, "кит"])
def test_invalid_source_names_are_rejected(tmp_path: Path, name: str) -> None:
    src = Source(name=name, path=_dir_with_migration(tmp_path))
    with pytest.raises(MigrateError, match="invalid source name"):
        src.validate()


def test_source_name_at_length_limit_is_accepted(tmp_path: Path) -> None:
    Source(name="a" * 45, path=_dir_with_migration(tmp_path)).validate()


def test_missing_directory_is_rejected() -> None:
    with pytest.raises(MigrateError, match="is not a directory"):
        Source(name="kit", path="/definitely/not/here").validate()


def test_empty_directory_is_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(MigrateError, match="no migrations in"):
        Source(name="kit", path=str(empty)).validate()


def test_startup_sources_sorts_kits_and_appends_app(tmp_path: Path) -> None:
    path = _dir_with_migration(tmp_path)
    kits = (Source(name="outboxkit", path=path), Source(name="auditkit", path=path))
    ordered = startup_sources(kits, "/srv/migrations")
    assert [s.name for s in ordered] == ["auditkit", "outboxkit", AppSourceName]
    assert ordered[-1].path == "/srv/migrations"


def test_startup_sources_without_app_path(tmp_path: Path) -> None:
    path = _dir_with_migration(tmp_path)
    ordered = startup_sources((Source(name="auditkit", path=path),), "")
    assert [s.name for s in ordered] == ["auditkit"]


def test_migrate_up_all_rejects_duplicate_source_names(tmp_path: Path) -> None:
    path = _dir_with_migration(tmp_path)
    src = Source(name="kit", path=path)
    with pytest.raises(MigrateError, match="duplicate source name"):
        migrate_up_all("postgresql://unused/db", src, src)


def test_migrate_up_all_validates_before_connecting(tmp_path: Path) -> None:
    bad = Source(name="Bad", path=_dir_with_migration(tmp_path))
    with pytest.raises(MigrateError, match="invalid source name"):
        migrate_up_all("postgresql://unused/db", bad)


def test_migrate_up_all_without_sources_is_noop() -> None:
    migrate_up_all("postgresql://unused/db")


async def test_migrate_provider_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _dir_with_migration(tmp_path)
    calls: list[tuple[str, tuple[str, ...], float]] = []
    threads: list[threading.Thread] = []

    def fake_migrate_up_all(dsn: str, *sources: Source, timeout: float) -> None:
        threads.append(threading.current_thread())
        calls.append((dsn, tuple(s.name for s in sources), timeout))

    monkeypatch.setattr("pylibinfra.pgkit.module.migrate_up_all", fake_migrate_up_all)

    class AppProvider(Provider):
        scope = Scope.APP

        @provide
        def pool_config(self) -> PoolConfig:
            return PoolConfig.model_validate({"dsn": "postgresql://app:secret@db/app"})

        @provide
        def migrate_config(self) -> MigrateRunnerConfig:
            return MigrateRunnerConfig.model_validate(
                {"migrations_path": "/srv/migrations", "migrate_timeout": 7.0}
            )

        @provide
        def sources(self) -> MigrationSources:
            return (Source(name="outboxkit", path=path), Source(name="auditkit", path=path))

    container = make_async_container(PgMigrateProvider(), AppProvider())
    try:
        assert isinstance(await container.get(Migrated), Migrated)
    finally:
        await container.close()

    # yoyo синхронный: прогон в главном потоке заморозил бы loop на всё время миграций.
    assert len(threads) == 1
    assert threads[0] is not threading.main_thread()
    assert calls == [
        ("postgresql://app:secret@db/app", ("auditkit", "outboxkit", AppSourceName), 7.0)
    ]


def test_advisory_lock_key_is_stable_and_fits_bigint() -> None:
    key = advisory_lock_key()
    assert key == advisory_lock_key()
    assert -(2**63) <= key < 2**63


@pytest.mark.parametrize(
    ("migration_id", "version"),
    [("0001_init", 1), ("0042_add_index", 42), ("init", 0), ("", 0)],
)
def test_version_of(migration_id: str, version: int) -> None:
    assert _version_of(migration_id) == version
