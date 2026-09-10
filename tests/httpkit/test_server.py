import asyncio
import socket

import httpx
import pytest

from pylibinfra.httpkit import Config, ListenError, Server
from pylibinfra.httpkit.server import split_addr
from pylibinfra.logkit import nop
from pylibinfra.testkit.logtest import Capture


@pytest.mark.parametrize(
    ("addr", "want"),
    [
        (":8080", ("", 8080)),
        ("127.0.0.1:0", ("127.0.0.1", 0)),
        ("0.0.0.0:9000", ("0.0.0.0", 9000)),
        ("[::1]:9000", ("::1", 9000)),
    ],
)
def test_split_addr(addr: str, want: tuple[str, int]) -> None:
    assert split_addr(addr) == want


def test_split_addr_rejects_garbage() -> None:
    with pytest.raises(ListenError):
        split_addr("host:port")


def test_listen_is_idempotent_and_reports_actual_addr(log: Capture) -> None:
    srv = Server(Config(addr="127.0.0.1:0"), log)
    assert srv.addr() == ""
    srv.listen()
    first = srv.addr()
    srv.listen()
    assert srv.addr() == first
    host, port = first.rsplit(":", 1)
    assert host == "127.0.0.1"
    assert int(port) > 0
    srv._close_socket()


def test_subsecond_timeouts_reach_uvicorn_rounded_up(log: Capture) -> None:
    srv = Server(Config(addr="127.0.0.1:0", idle_timeout=0.5, shutdown_timeout=0.5), log)

    # усечение до int дало бы 0 — «не держать keep-alive» и «гасить без ожидания»
    assert srv._uvicorn.config.timeout_keep_alive == 1
    assert srv._uvicorn.config.timeout_graceful_shutdown == 1


def test_listen_on_busy_port_fails(log: Capture) -> None:
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    busy = holder.getsockname()[1]
    try:
        srv = Server(Config(addr=f"127.0.0.1:{busy}"), log)
        with pytest.raises(ListenError, match="listen"):
            srv.listen()
    finally:
        holder.close()


async def test_serve_and_stop_over_real_socket(log: Capture) -> None:
    srv = Server(Config(addr="127.0.0.1:0", shutdown_timeout=5.0), log)

    @srv.app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    srv.listen()
    addr = srv.addr()
    task = asyncio.create_task(srv.serve())
    for _ in range(200):
        if srv._uvicorn.started:
            break
        await asyncio.sleep(0.01)

    async with httpx.AsyncClient(base_url=f"http://{addr}") as c:
        resp = await c.get("/ping")
        assert resp.status_code == 200
        assert resp.json() == {"pong": "ok"}
        assert resp.headers["x-request-id"]

    await srv.stop()
    await task
    assert task.done()


async def test_stop_without_serve_is_noop(log: Capture) -> None:
    srv = Server(Config(addr="127.0.0.1:0"), log)
    srv.listen()
    await srv.stop()
    assert srv.addr() == ""


def test_security_schemes_reach_openapi(log: Capture) -> None:
    schemes = {"Bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}
    srv = Server(Config(addr="127.0.0.1:0"), log, security_schemes=schemes)
    assert srv.app.openapi()["components"]["securitySchemes"] == schemes


def test_consumer_middleware_is_innermost(log: Capture) -> None:
    from starlette.middleware import Middleware
    from starlette.types import ASGIApp, Receive, Scope, Send

    seen: list[str] = []

    class Tenant:
        def __init__(self, app: ASGIApp) -> None:
            self._app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            seen.append(scope["path"])
            await self._app(scope, receive, send)

    srv = Server(
        Config(addr="127.0.0.1:0"), log, middlewares=(Middleware(Tenant),), autostart=False
    )
    assert srv.autostart is False
    assert srv.app.user_middleware[-1].cls is Tenant


async def test_stop_cancels_serve_after_shutdown_timeout(log: Capture) -> None:
    srv = Server(Config(addr="127.0.0.1:0", shutdown_timeout=0.01), log)

    async def hang() -> None:
        await asyncio.sleep(10)

    task = asyncio.create_task(hang())
    srv._serve_task = task
    await srv.stop()
    assert task.cancelled() or task.cancelling()
    task.cancel()


async def test_serve_after_stop_does_not_rebind(log: Capture) -> None:
    srv = Server(Config(addr="127.0.0.1:0"), log)
    srv.listen()
    await srv.stop()
    await srv.serve()
    assert srv.addr() == ""


@pytest.mark.skipif(not socket.has_ipv6, reason="IPv6 недоступен")
async def test_bind_all_is_dual_stack() -> None:
    srv = Server(Config(addr=":0"), nop())
    srv.listen()
    try:
        addr = srv.addr()
        assert addr.startswith("[::]:")
        port = int(addr.rsplit(":", 1)[1])
        task = asyncio.create_task(srv.serve())
        try:
            async with httpx.AsyncClient() as client:
                v4 = await client.get(f"http://127.0.0.1:{port}/health")
                assert v4.status_code == 200
                v6 = await client.get(f"http://[::1]:{port}/health")
                assert v6.status_code == 200
        finally:
            await srv.stop()
            await task
    finally:
        srv._close_socket()
