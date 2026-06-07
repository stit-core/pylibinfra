from datetime import UTC, datetime

from pylibinfra.auditkit import Entry, Recorder, Store, StoreRecorder


class FakeStore:
    def __init__(self) -> None:
        self.recorded: list[Entry] = []

    async def record(self, entry: Entry) -> None:
        self.recorded.append(entry)


def test_fake_store_satisfies_protocol() -> None:
    assert isinstance(FakeStore(), Store)


def test_store_recorder_satisfies_recorder_protocol() -> None:
    assert isinstance(StoreRecorder(FakeStore()), Recorder)


async def test_recorder_passes_entry_to_store() -> None:
    store = FakeStore()
    rec = StoreRecorder(store)
    at = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    entry = Entry(
        action="user.create",
        resource="user",
        actor_id="actor-1",
        resource_id="u-42",
        metadata={"ip": "127.0.0.1", "n": 7},
        at=at,
        ip="127.0.0.1",
        request_id="req-xyz",
    )

    await rec.record(entry)

    assert store.recorded == [entry]
    got = store.recorded[0]
    assert got.action == "user.create"
    assert got.resource == "user"
    assert got.actor_id == "actor-1"
    assert got.resource_id == "u-42"
    assert got.metadata == {"ip": "127.0.0.1", "n": 7}
    assert got.at == at
    assert got.ip == "127.0.0.1"
    assert got.request_id == "req-xyz"


def test_entry_defaults_are_empty() -> None:
    entry = Entry(action="login", resource="session")

    assert entry.actor_id == ""
    assert entry.resource_id == ""
    assert entry.ip == ""
    assert entry.request_id == ""
    assert entry.metadata == {}
    assert entry.at is None
