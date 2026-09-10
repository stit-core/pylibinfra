"""Кейсы контракта `storagekit.Storage`/`Presigner` и их прогон."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import unquote

from pylibinfra import storagekit

Case = Callable[["Subject"], Awaitable[None]]
SubjectFactory = Callable[[], "Subject | Awaitable[Subject]"]

_MISSING_KEY = "no-such-key.bin"


@dataclass(frozen=True, slots=True)
class Subject:
    """Реализация под контрактом.

    `keys` опционален: с ним контракт дополнительно проверяет, что отвергнутые
    загрузки не оставляют объектов.
    """

    storage: storagekit.Storage
    presigner: storagekit.Presigner
    keys: Callable[[], Awaitable[Sequence[str]]] | None = None


async def run_contract(new_subject: SubjectFactory) -> None:
    """Прогоняет весь контракт; `new_subject` зовётся на кейс и отдаёт изолированное хранилище."""
    for name in CASES:
        await run_case(name, await _subject(new_subject))


async def run_case(name: str, subject: Subject) -> None:
    """Прогоняет один кейс контракта; неизвестное имя — `KeyError`."""
    try:
        await CASES[name](subject)
    except AssertionError as exc:
        raise AssertionError(f"storagetest: {name}: {exc}") from exc


async def _subject(new_subject: SubjectFactory) -> Subject:
    made = new_subject()
    if inspect.isawaitable(made):
        return await made
    return made


async def _put_get_stat_delete(subject: Subject) -> None:
    body = b"%PDF-1.7\n" * 64

    ref = await subject.storage.put(body, "application/pdf", len(body))
    assert ref.size == len(body), f"ref.size = {ref.size}"
    assert ref.content_type == "application/pdf", f"ref.content_type = {ref.content_type!r}"
    assert ref.key.endswith(".pdf"), f"ref.key = {ref.key!r}, want .pdf suffix"
    assert ref.url.endswith(ref.key), f"ref.url = {ref.url!r}, want suffix {ref.key!r}"

    reader, info = await subject.storage.get(ref.key)
    async with reader:
        stored = await reader.read()
    assert stored == body, f"get returned {len(stored)} bytes, want {len(body)}"
    assert info.size == len(body), f"info.size = {info.size}"
    assert info.content_type == "application/pdf", f"info.content_type = {info.content_type!r}"
    assert info.etag, "info.etag is empty"
    assert info.last_modified is not None, "info.last_modified is empty"

    stat = await subject.storage.stat(ref.key)
    assert stat.size == info.size, f"stat.size = {stat.size}, want {info.size}"
    assert stat.content_type == info.content_type, f"stat.content_type = {stat.content_type!r}"

    await subject.storage.delete(ref.key)
    await subject.storage.delete(ref.key)
    await _expect(storagekit.NotFoundError, subject.storage.stat(ref.key), "stat after delete")


async def _put_streams_unknown_size(subject: Subject) -> None:
    body = b"x" * 100_000

    ref = await subject.storage.put(_stream(body), "application/octet-stream", -1)
    assert ref.size == len(body), f"ref.size = {ref.size}, want {len(body)}"

    reader, _ = await subject.storage.get(ref.key)
    async with reader:
        stored = await reader.read()
    assert stored == body, f"stored body differs: {len(stored)} bytes"


async def _put_rejects_size_mismatch(subject: Subject) -> None:
    body = b"0123456789"

    for source, make in (("bytes", lambda: body), ("stream", lambda: _stream(body))):
        for declared in (4, 40):
            await _expect(
                storagekit.SizeMismatchError,
                subject.storage.put(make(), "application/pdf", declared),
                f"put({source}, size={declared})",
            )
            await _expect(
                storagekit.UploadFailedError,
                subject.storage.put(make(), "application/pdf", declared),
                f"put({source}, size={declared})",
            )
    if subject.keys is not None:
        left = await subject.keys()
        assert not left, f"rejected uploads left objects: {list(left)}"

    ref = await subject.storage.put(_stream(body), "application/pdf", len(body))
    assert ref.size == len(body), f"stream with exact size: ref.size = {ref.size}"


async def _put_reports_a_failing_body(subject: Subject) -> None:
    for declared in (64, -1):
        await _expect(
            storagekit.UploadFailedError,
            subject.storage.put(_failing_stream(), "application/octet-stream", declared),
            f"put(failing body, size={declared})",
        )
    if subject.keys is not None:
        left = await subject.keys()
        assert not left, f"failed uploads left objects: {list(left)}"


async def _put_normalizes_content_type(subject: Subject) -> None:
    ref = await subject.storage.put(b"payload", "", 7)
    assert ref.content_type == storagekit.DEFAULT_CONTENT_TYPE, (
        f"ref.content_type = {ref.content_type!r}"
    )

    stat = await subject.storage.stat(ref.key)
    assert stat.content_type == storagekit.DEFAULT_CONTENT_TYPE, (
        f"stored content-type = {stat.content_type!r}"
    )


async def _missing_object(subject: Subject) -> None:
    await _expect(storagekit.NotFoundError, subject.storage.get(_MISSING_KEY), "get missing")
    await _expect(storagekit.NotFoundError, subject.storage.stat(_MISSING_KEY), "stat missing")
    await subject.storage.delete(_MISSING_KEY)


async def _empty_key_rejected(subject: Subject) -> None:
    await _expect(storagekit.DeleteFailedError, subject.storage.delete(""), 'delete("")')
    await _expect(storagekit.GetFailedError, subject.storage.get(""), 'get("")')
    await _expect(storagekit.StatFailedError, subject.storage.stat(""), 'stat("")')


async def _presign_validates_args(subject: Subject) -> None:
    for ttl in (0.0, 0.5, storagekit.MAX_PRESIGN_TTL + 1):
        await _expect(
            storagekit.PresignFailedError,
            subject.presigner.presign_get("obj.bin", ttl),
            f"presign_get(ttl={ttl:g})",
        )
        await _expect(
            storagekit.PresignFailedError,
            subject.presigner.presign_put("obj.bin", ttl),
            f"presign_put(ttl={ttl:g})",
        )
    await _expect(
        storagekit.PresignFailedError, subject.presigner.presign_get("", 60), 'presign_get("")'
    )
    await _expect(
        storagekit.PresignFailedError, subject.presigner.presign_put("", 60), 'presign_put("")'
    )
    for ttl in (storagekit.MIN_PRESIGN_TTL, storagekit.MAX_PRESIGN_TTL):
        assert await subject.presigner.presign_get("obj.bin", ttl), f"presign_get(ttl={ttl:g})"


async def _presign_get_and_put(subject: Subject) -> None:
    key = "dir/obj name.bin"

    link = await subject.presigner.presign_get(
        key,
        15 * 60,
        response_content_disposition='attachment; filename="report.pdf"',
        response_content_type="application/pdf",
    )
    assert " " not in link, f"presigned GET {link!r} keeps a raw space in the key"
    decoded = unquote(link.replace("+", " "))
    for want in (
        "dir/obj name.bin",
        "response-content-disposition",
        "report.pdf",
        "application/pdf",
    ):
        assert want in decoded, f"presigned GET {link!r} misses {want!r}"

    upload = await subject.presigner.presign_put(key, 15 * 60)
    assert "obj" in unquote(upload), f"presigned PUT {upload!r} misses the key"


CASES: Mapping[str, Case] = MappingProxyType(
    {
        "put_get_stat_delete": _put_get_stat_delete,
        "put_streams_unknown_size": _put_streams_unknown_size,
        "put_rejects_size_mismatch": _put_rejects_size_mismatch,
        "put_reports_a_failing_body": _put_reports_a_failing_body,
        "put_normalizes_content_type": _put_normalizes_content_type,
        "missing_object": _missing_object,
        "empty_key_rejected": _empty_key_rejected,
        "presign_validates_args": _presign_validates_args,
        "presign_get_and_put": _presign_get_and_put,
    }
)
"""Кейсы контракта: имя → проверка; порядок — порядок прогона `run_contract`."""


async def _stream(data: bytes, chunk: int = 8192) -> AsyncIterator[bytes]:
    """Источник неизвестной длины: контракт `size` проверяется и на таком теле."""
    for start in range(0, len(data), chunk):
        yield data[start : start + chunk]


async def _failing_stream() -> AsyncIterator[bytes]:
    """Источник, обрывающийся посреди тела: файл на сети, отвалившийся upstream."""
    yield b"head"
    raise _SourceGoneError("source is gone")


class _SourceGoneError(Exception):
    """Отказ источника тела внутри контрактного кейса."""


async def _expect(error: type[Exception], awaitable: Awaitable[object], what: str) -> None:
    try:
        await awaitable
    except error:
        return
    except Exception as exc:
        raise AssertionError(
            f"{what}: raised {type(exc).__name__}: {exc}, want {error.__name__}"
        ) from exc
    raise AssertionError(f"{what}: no error, want {error.__name__}")
