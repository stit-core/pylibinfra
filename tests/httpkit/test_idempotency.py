import asyncio
import base64
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request, Response
from starlette.types import Message, Receive, Scope, Send

from pylibinfra.clockkit import Tickable
from pylibinfra.httpkit import (
    Config,
    IdempotencyMiddleware,
    IdempotencyStore,
    MemoryStore,
    bind_subject,
    build_app,
    subject,
    unbind_subject,
)
from pylibinfra.httpkit.idempotency import SWEEP_EVERY, fingerprint
from pylibinfra.testkit.logtest import Capture

from .conftest import client, one

_START = datetime(2026, 1, 1, tzinfo=UTC)


def _app(log: Capture, store: IdempotencyStore, **cfg: object) -> tuple[FastAPI, list[int]]:
    calls: list[int] = []
    app = build_app(Config(idempotency=cfg or {}), log, store=store)

    @app.post("/create")
    async def create() -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    @app.post("/fail")
    async def fail() -> Response:
        calls.append(1)
        return Response(status_code=500, content=b'{"code":"BOOM"}')

    @app.post("/big")
    async def big() -> dict[str, str]:
        calls.append(1)
        return {"payload": "x" * 5000}

    @app.post("/boom")
    async def boom() -> dict[str, str]:
        calls.append(1)
        raise RuntimeError("kaboom")

    @app.get("/read")
    async def read() -> dict[str, bool]:
        calls.append(1)
        return {"ok": True}

    return app, calls


def test_memory_store_satisfies_protocol() -> None:
    assert isinstance(MemoryStore(), IdempotencyStore)


def test_fingerprint_covers_query_and_body() -> None:
    base = fingerprint("POST", "/create", "", b"{}")
    assert base == fingerprint("POST", "/create", "", b"{}")
    assert base != fingerprint("POST", "/create", "dry_run=true", b"{}")
    assert base != fingerprint("POST", "/create", "", b'{"a":1}')
    assert base != fingerprint("PUT", "/create", "", b"{}")
    # канонизация query: порядок параметров не меняет отпечаток
    assert fingerprint("POST", "/c", "b=2&a=1", b"") == fingerprint("POST", "/c", "a=1&b=2", b"")


async def test_memory_store_reserve_get_set_release() -> None:
    store = MemoryStore()
    token, ok = await store.reserve("k", 60)
    assert ok is True
    assert token
    assert await store.get("k") is None

    _, ok2 = await store.reserve("k", 60)
    assert ok2 is False

    await store.release("k", "other-token")
    assert (await store.reserve("k", 60))[1] is False

    await store.release("k", token)
    await store.set("k", b"record", 60)
    assert await store.get("k") == b"record"
    # запись тоже держит ключ: второй резерв невозможен
    assert (await store.reserve("k", 60))[1] is False


async def test_memory_store_expiry_and_sweep() -> None:
    clock = Tickable(_START)
    store = MemoryStore(clock)
    await store.set("k", b"v", 10)
    clock.advance(11)
    assert await store.get("k") is None

    for i in range(20):
        await store.set(f"k{i}", b"v", 10)
    clock.advance(SWEEP_EVERY + 1)
    await store.set("fresh", b"v", 10)
    assert store.size() == 1


async def test_replay_returns_stored_response(
    log: Capture, read_metric: Callable[..., float]
) -> None:
    app, calls = _app(log, MemoryStore())
    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        first = await c.post("/create", headers=headers)
        second = await c.post("/create", headers=headers)

    assert len(calls) == 1
    assert first.json() == second.json() == {"n": 1}
    assert "idempotent-replayed" not in first.headers
    assert second.headers["idempotent-replayed"] == "true"
    assert second.headers["content-type"] == "application/json"
    # длина известна: replay уходит с Content-Length, как и оригинал, а не chunked
    assert second.headers["content-length"] == first.headers["content-length"]
    assert read_metric("http_idempotency_total", {"result": "stored"}) == 1
    assert read_metric("http_idempotency_total", {"result": "replayed"}) == 1


async def test_different_body_is_conflict(log: Capture, read_metric: Callable[..., float]) -> None:
    app, calls = _app(log, MemoryStore())
    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        await c.post("/create", headers=headers, json={"a": 1})
        resp = await c.post("/create", headers=headers, json={"a": 2})

    assert len(calls) == 1
    assert resp.status_code == 422
    assert resp.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert read_metric("http_idempotency_total", {"result": "conflict"}) == 1


async def test_different_query_is_conflict(log: Capture) -> None:
    app, _ = _app(log, MemoryStore())
    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        await c.post("/create", headers=headers)
        resp = await c.post("/create?dry_run=true", headers=headers)
    assert resp.status_code == 422


async def test_parallel_repeat_is_in_progress(
    log: Capture, read_metric: Callable[..., float]
) -> None:
    gate = asyncio.Event()
    store = MemoryStore()
    app = build_app(Config(), log, store=store)

    @app.post("/slow")
    async def slow() -> dict[str, bool]:
        await gate.wait()
        return {"ok": True}

    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        first = asyncio.create_task(c.post("/slow", headers=headers))
        await asyncio.sleep(0)
        second = await c.post("/slow", headers=headers)
        gate.set()
        await first

    assert second.status_code == 409
    assert second.json()["code"] == "IDEMPOTENCY_IN_PROGRESS"
    assert read_metric("http_idempotency_total", {"result": "in_progress"}) == 1


async def test_in_progress_is_not_also_counted_as_error(
    log: Capture, read_metric: Callable[..., float]
) -> None:
    class BusyStore(MemoryStore):
        async def get(self, key: str) -> bytes | None:
            # ключ занят, а перепроверка записи после reserve не удалась
            if self._probed:
                raise ConnectionError("store down")
            self._probed = True
            return None

        async def reserve(self, key: str, ttl: float) -> tuple[str, bool]:
            return "", False

        _probed = False

    app, calls = _app(log, BusyStore())
    async with client(app) as c:
        resp = await c.post("/create", headers={"Idempotency-Key": "k1"})

    assert resp.status_code == 409
    assert calls == []
    assert read_metric("http_idempotency_total", {"result": "in_progress"}) == 1
    # исход один: провал перепроверки не добавляет отдельный result=error
    assert read_metric("http_idempotency_total", {"result": "error"}) == 0


async def test_server_error_is_not_stored(log: Capture, read_metric: Callable[..., float]) -> None:
    app, calls = _app(log, MemoryStore())
    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        await c.post("/fail", headers=headers)
        second = await c.post("/fail", headers=headers)

    assert len(calls) == 2
    assert second.status_code == 500
    assert read_metric("http_idempotency_total", {"result": "skipped"}) == 2


async def test_oversized_response_is_not_stored(
    log: Capture, read_metric: Callable[..., float]
) -> None:
    app, calls = _app(log, MemoryStore(), max_body_bytes=1024)
    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        await c.post("/big", headers=headers)
        await c.post("/big", headers=headers)

    assert len(calls) == 2
    assert read_metric("http_idempotency_total", {"result": "skipped"}) == 2


async def test_oversized_request_skips_dedup(
    log: Capture, read_metric: Callable[..., float]
) -> None:
    app, calls = _app(log, MemoryStore(), max_body_bytes=64)
    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        payload = {"a": "x" * 500}
        first = await c.post("/create", headers=headers, json=payload)
        second = await c.post("/create", headers=headers, json=payload)

    assert first.status_code == second.status_code == 200
    assert len(calls) == 2
    assert read_metric("http_idempotency_total", {"result": "skipped"}) == 2


async def _chunks(payload: bytes, size: int) -> AsyncIterator[bytes]:
    for start in range(0, len(payload), size):
        yield payload[start : start + size]


async def test_oversized_chunked_request_reaches_handler_intact(log: Capture) -> None:
    seen: list[int] = []
    app = build_app(Config(idempotency={"max_body_bytes": 64}), log, store=MemoryStore())

    @app.post("/upload")
    async def upload(request: Request) -> dict[str, int]:
        seen.append(len(await request.body()))
        return {"size": seen[-1]}

    payload = b"a" * 500
    async with client(app) as c:
        resp = await c.post(
            "/upload", headers={"Idempotency-Key": "k1"}, content=_chunks(payload, 16)
        )

    # прочитанный на дедупликацию префикс возвращается обработчику вместе с хвостом
    assert resp.json() == {"size": len(payload)}
    assert seen == [len(payload)]


async def test_disconnect_before_body_does_not_break_the_middleware(log: Capture) -> None:
    reached: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        reached.append("handler")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    middleware = IdempotencyMiddleware(app, Config().idempotency, MemoryStore(), log=log)
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/create",
        "query_string": b"",
        "headers": [(b"idempotency-key", b"k1")],
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, receive, send)

    assert reached == ["handler"]
    assert sent[0]["status"] == 200


async def test_handler_exception_releases_reservation(log: Capture) -> None:
    store = MemoryStore()
    app, calls = _app(log, store)
    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        first = await c.post("/boom", headers=headers)
        second = await c.post("/boom", headers=headers)

    assert first.status_code == second.status_code == 500
    # резерв снят исключением обработчика, поэтому повтор выполняется, а не ловит 409
    assert len(calls) == 2


async def test_non_mutating_and_keyless_requests_bypass(log: Capture) -> None:
    app, calls = _app(log, MemoryStore())
    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        for _ in range(3):
            await c.get("/read", headers=headers)
        await c.post("/create")
        await c.post("/create")
    assert len(calls) == 5


async def test_key_is_scoped_to_credentials(log: Capture) -> None:
    app, calls = _app(log, MemoryStore())
    async with client(app) as c:
        key = {"Idempotency-Key": "k1"}
        await c.post("/create", headers={**key, "Authorization": "Bearer alice"})
        await c.post("/create", headers={**key, "Authorization": "Bearer bob"})
    assert len(calls) == 2


async def test_key_is_scoped_to_subject(log: Capture) -> None:
    store = MemoryStore()
    calls: list[str] = []

    async def extract(request: Request) -> object:
        who = request.headers.get("X-Who", "")
        bind_subject(who)
        return {"sub": who}

    app = build_app(Config(), log, store=store, auth=extract)

    @app.post("/create")
    async def create(request: Request) -> dict[str, object]:
        calls.append(str(request.state.auth))
        return {"n": len(calls)}

    async with client(app) as c:
        key = {"Idempotency-Key": "k1"}
        # один и тот же субъект с разными сырыми токенами — одна операция
        first = await c.post("/create", headers={**key, "X-Who": "u1", "Authorization": "t1"})
        second = await c.post("/create", headers={**key, "X-Who": "u1", "Authorization": "t2"})
        third = await c.post("/create", headers={**key, "X-Who": "u2", "Authorization": "t3"})

    assert first.json() == second.json() == {"n": 1}
    assert third.json() == {"n": 2}
    assert len(calls) == 2


async def test_subject_bound_above_the_chain_survives(log: Capture) -> None:
    seen: list[str] = []
    app, _ = _app(log, MemoryStore())

    @app.post("/whoami")
    async def whoami() -> dict[str, str]:
        seen.append(subject())
        return {"sub": subject()}

    async with client(app) as c:
        token = bind_subject("outer-user")
        try:
            first = await c.post("/whoami", headers={"Idempotency-Key": "k1"})
        finally:
            unbind_subject(token)

    # middleware не затирает субъект, положенный выше по цепочке: по нему scope'ится ключ
    assert seen == ["outer-user"]
    assert first.json() == {"sub": "outer-user"}


async def test_anonymous_request_is_not_deduplicated(
    log: Capture, read_metric: Callable[..., float]
) -> None:
    calls: list[int] = []

    async def extract(request: Request) -> object:
        if not request.headers.get("Authorization"):
            raise ValueError("anonymous")
        return {"sub": "u1"}

    app = build_app(Config(), log, store=MemoryStore(), auth=extract)

    @app.post("/create")
    async def create() -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        first = await c.post("/create", headers=headers)
        second = await c.post("/create", headers=headers)

    # дедупликации нет (result=skipped), а сам запрос отбивает deny-by-default auth-гейт
    assert [first.status_code, second.status_code] == [401, 401]
    assert calls == []
    assert read_metric("http_idempotency_total", {"result": "skipped"}) == 2


async def test_idempotency_alone_passes_anonymous_through(
    log: Capture, read_metric: Callable[..., float]
) -> None:
    calls: list[int] = []

    async def extract(request: Request) -> object:
        raise ValueError("anonymous")

    app = FastAPI()

    @app.post("/create")
    async def create() -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    app.add_middleware(
        IdempotencyMiddleware,
        cfg=Config().idempotency,
        store=MemoryStore(),
        extractor=extract,
        log=log,
    )
    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        await c.post("/create", headers=headers)
        await c.post("/create", headers=headers)

    assert len(calls) == 2
    assert read_metric("http_idempotency_total", {"result": "skipped"}) == 2


class _BrokenStore:
    def __init__(self, fail_on: str = "get") -> None:
        self.fail_on = fail_on
        self.released: list[str] = []

    async def reserve(self, key: str, ttl: float) -> tuple[str, bool]:
        if self.fail_on == "reserve":
            raise ConnectionError("store down")
        return "token", True

    async def get(self, key: str) -> bytes | None:
        if self.fail_on == "get":
            raise ConnectionError("store down")
        return None

    async def set(self, key: str, record: bytes, ttl: float) -> None:
        if self.fail_on == "set":
            raise ConnectionError("store down")

    async def release(self, key: str, token: str) -> None:
        self.released.append(token)


@pytest.mark.parametrize("fail_on", ["get", "reserve"])
async def test_store_failure_is_fail_open(
    log: Capture, read_metric: Callable[..., float], fail_on: str
) -> None:
    app, calls = _app(log, _BrokenStore(fail_on))
    async with client(app) as c:
        resp = await c.post("/create", headers={"Idempotency-Key": "k1"})

    assert resp.status_code == 200
    assert len(calls) == 1
    assert read_metric("http_idempotency_total", {"result": "error"}) == 1


@pytest.mark.parametrize("fail_on", ["get", "reserve"])
async def test_store_failure_fail_closed_answers_503(log: Capture, fail_on: str) -> None:
    app, calls = _app(log, _BrokenStore(fail_on), fail_closed=True)
    async with client(app) as c:
        resp = await c.post("/create", headers={"Idempotency-Key": "k1"})

    assert resp.status_code == 503
    assert resp.json()["code"] == "IDEMPOTENCY_UNAVAILABLE"
    assert calls == []


async def test_set_failure_releases_reservation(
    log: Capture, read_metric: Callable[..., float]
) -> None:
    store = _BrokenStore("set")
    app, calls = _app(log, store)
    async with client(app) as c:
        resp = await c.post("/create", headers={"Idempotency-Key": "k1"})

    assert resp.status_code == 200
    assert len(calls) == 1
    assert store.released == ["token"]
    assert read_metric("http_idempotency_total", {"result": "error"}) == 1


class _SlowStore(MemoryStore):
    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay
        self.completed: list[str] = []
        self.cancelled: list[str] = []

    async def set(self, key: str, record: bytes, ttl: float) -> None:
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled.append(key)
            raise
        await super().set(key, record, ttl)
        self.completed.append(key)


async def test_store_timeout_is_bounded(log: Capture, read_metric: Callable[..., float]) -> None:
    store = _SlowStore(0.2)
    app, _ = _app(log, store, store_timeout=0.01)
    async with client(app) as c:
        resp = await c.post("/create", headers={"Idempotency-Key": "k1"})

    assert resp.status_code == 200
    assert read_metric("http_idempotency_total", {"result": "error"}) == 1
    # дедлайн store снимает и саму операцию: зависший store не оставляет задачу висеть
    await asyncio.sleep(0)
    assert len(store.cancelled) == 1
    assert store.completed == []


async def test_store_write_survives_client_cancellation(log: Capture) -> None:
    store = _SlowStore(0.05)
    app, calls = _app(log, store)

    async with client(app) as c:
        task = asyncio.create_task(c.post("/create", headers={"Idempotency-Key": "k1"}))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # запись отвязана от отмены клиента: ретрай получает replay, а не второе выполнение
        await asyncio.sleep(0.1)
        resp = await c.post("/create", headers={"Idempotency-Key": "k1"})

    assert store.completed
    assert resp.headers["idempotent-replayed"] == "true"
    assert len(calls) == 1


async def test_replay_reproduces_allowlisted_headers(log: Capture) -> None:
    store = MemoryStore()
    app = build_app(Config(), log, store=store)

    @app.post("/create")
    async def create() -> Response:
        return Response(
            status_code=201,
            content=b'{"id":"7"}',
            media_type="application/json",
            headers={"Location": "/users/7", "ETag": 'W/"1"', "X-Internal": "leak"},
        )

    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        first = await c.post("/create", headers=headers)
        second = await c.post("/create", headers=headers)

    assert first.status_code == second.status_code == 201
    assert second.headers["location"] == "/users/7"
    assert second.headers["etag"] == 'W/"1"'
    assert second.headers["idempotent-replayed"] == "true"
    # заголовки вне allowlist не воспроизводятся — их ставит текущая цепочка
    assert "x-internal" not in second.headers
    assert second.headers["x-request-id"] != first.headers["x-request-id"]


class _CorruptStore(MemoryStore):
    async def get(self, key: str) -> bytes | None:
        stored = await super().get(key)
        return b"not-json" if stored is not None else None


async def test_corrupted_record_is_treated_as_absent(log: Capture) -> None:
    app, calls = _app(log, _CorruptStore())
    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        await c.post("/create", headers=headers)
        second = await c.post("/create", headers=headers)
    assert second.status_code == 409
    assert len(calls) == 1


async def test_release_failure_is_logged_not_raised(
    log: Capture, read_metric: Callable[..., float]
) -> None:
    class NoRelease(MemoryStore):
        async def release(self, key: str, token: str) -> None:
            raise ConnectionError("store down")

    app, _ = _app(log, NoRelease())
    async with client(app) as c:
        resp = await c.post("/fail", headers={"Idempotency-Key": "k1"})

    assert resp.status_code == 500
    assert one(log, "idempotency store failed").value("op") == "release"
    assert read_metric("http_idempotency_total", {"result": "skipped"}) == 1


async def test_reserved_key_replays_record_that_appeared_meanwhile(
    log: Capture, read_metric: Callable[..., float]
) -> None:
    class RaceStore(MemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.gets = 0
            self.record: bytes | None = None

        async def get(self, key: str) -> bytes | None:
            self.gets += 1
            # первый запрос ещё «в полёте», ответ появляется между reserve и повторным get
            return self.record if self.gets > 1 else None

        async def reserve(self, key: str, ttl: float) -> tuple[str, bool]:
            return "", False

    store = RaceStore()
    app, calls = _app(log, store)
    async with client(app) as c:
        headers = {"Idempotency-Key": "k1"}
        store.record = json.dumps(
            {
                "fp": fingerprint("POST", "/create", "", b""),
                "status": 201,
                "header": {"content-type": ["application/json"]},
                "body": base64.b64encode(b'{"n":1}').decode(),
            }
        ).encode()
        resp = await c.post("/create", headers=headers)

    assert resp.status_code == 201
    assert resp.json() == {"n": 1}
    assert resp.headers["idempotent-replayed"] == "true"
    assert calls == []
    assert read_metric("http_idempotency_total", {"result": "replayed"}) == 1
