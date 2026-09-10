"""Логгер-перехватчик для проверки текста и полей записей timerkit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class Record:
    level: str
    msg: str
    fields: dict[str, Any]


@dataclass
class CaptureLogger:
    records: list[Record] = field(default_factory=list)

    def debug(self, msg: str, **kv: Any) -> None:
        self.records.append(Record("debug", msg, kv))

    def info(self, msg: str, **kv: Any) -> None:
        self.records.append(Record("info", msg, kv))

    def warn(self, msg: str, **kv: Any) -> None:
        self.records.append(Record("warn", msg, kv))

    def error(self, msg: str, **kv: Any) -> None:
        self.records.append(Record("error", msg, kv))

    def with_(self, **kv: Any) -> CaptureLogger:
        return self

    def enabled(self, level: str) -> bool:
        return True

    def find(self, msg: str) -> Record | None:
        return next((r for r in self.records if r.msg == msg), None)


@pytest.fixture
def log() -> CaptureLogger:
    return CaptureLogger()
