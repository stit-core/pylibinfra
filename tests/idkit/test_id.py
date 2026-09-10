import threading
import uuid
from types import SimpleNamespace

import pytest

from pylibinfra import idkit
from pylibinfra.idkit import id as id_module


def test_new_v7_canonical_and_version() -> None:
    s = idkit.new_v7()
    assert len(s) == 36
    assert s == s.lower()
    idkit.validate(s)
    assert idkit.is_v7(s)
    assert uuid.UUID(s).version == 7


def test_strictly_increasing_within_process() -> None:
    ids = [idkit.new_v7() for _ in range(5000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_increasing_across_threads() -> None:
    out: list[str] = []
    lock = threading.Lock()

    def work() -> None:
        local = [idkit.new_v7() for _ in range(500)]
        with lock:
            out.extend(local)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(out)) == len(out)


def test_monotonic_when_the_sub_ms_counter_overflows(monkeypatch: pytest.MonkeyPatch) -> None:
    # 4096 значений rand_a исчерпываются внутри одной миллисекунды: метка
    # сдвигается вперёд, порядок выдачи сохраняется.
    frozen = 1_700_000_000_000_000_000
    monkeypatch.setattr(id_module, "time", SimpleNamespace(time_ns=lambda: frozen))

    ids = [idkit.new_v7() for _ in range(id_module._SEQ_MAX + 2)]

    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_monotonic_when_the_system_clock_goes_back(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_800_000_000_000_000_000
    monkeypatch.setattr(id_module, "time", SimpleNamespace(time_ns=lambda: now))
    before = idkit.new_v7()

    monkeypatch.setattr(id_module, "time", SimpleNamespace(time_ns=lambda: now - 10_000_000_000))
    after = [idkit.new_v7() for _ in range(3)]

    assert before < after[0]
    assert after == sorted(after)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-uuid",
        "0190E8A1-1C2D-7E3F-8A4B-5C6D7E8F9A0B",  # верхний регистр
        "{0190e8a1-1c2d-7e3f-8a4b-5c6d7e8f9a0b}",
        "urn:uuid:0190e8a1-1c2d-7e3f-8a4b-5c6d7e8f9a0b",
        "0190e8a11c2d7e3f8a4b5c6d7e8f9a0b",
    ],
)
def test_validate_rejects_non_canonical(bad: str) -> None:
    with pytest.raises(idkit.InvalidIDError):
        idkit.validate(bad)
    assert not idkit.is_v7(bad)


def test_is_v7_false_for_v4() -> None:
    v4 = str(uuid.uuid4())
    idkit.validate(v4)
    assert not idkit.is_v7(v4)
