from __future__ import annotations

from typing import Any

from pylibinfra.natskit.conn import _callbacks


class _RecordingLog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def debug(self, msg: str, **kv: Any) -> None:
        self.calls.append(("debug", msg))

    def info(self, msg: str, **kv: Any) -> None:
        self.calls.append(("info", msg))

    def warn(self, msg: str, **kv: Any) -> None:
        self.calls.append(("warn", msg))

    def error(self, msg: str, **kv: Any) -> None:
        self.calls.append(("error", msg))

    def with_(self, **kv: Any) -> _RecordingLog:
        return self


async def test_reconnect_callbacks_log_each_event() -> None:
    log = _RecordingLog()
    cbs = _callbacks(log)  # type: ignore[arg-type]

    await cbs["disconnected_cb"]()  # type: ignore[operator]
    await cbs["reconnected_cb"]()  # type: ignore[operator]
    await cbs["error_cb"](RuntimeError("boom"))  # type: ignore[operator]
    await cbs["closed_cb"]()  # type: ignore[operator]

    levels = [lvl for lvl, _ in log.calls]
    assert levels == ["warn", "info", "error", "warn"]
