"""MIGRATIONS — источник миграций кита для `pgkit.MigrationSources`."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from pylibinfra.pgkit import Source

MIGRATIONS = Source(name="auditkit", path=str(Path(str(files(__package__))) / "migrations"))
