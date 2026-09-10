from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx

from pylibinfra.clockkit import Clock
from pylibinfra.httpclient import BeforeAttempt, Client, Config


def cfg(**over: object) -> Config:
    """Конфиг с быстрыми паузами: тесты не ждут реального backoff'а."""
    base: dict[str, object] = {
        "timeout": 5.0,
        "max_retries": 3,
        "retry_wait": 0.001,
        "retry_max_wait": 0.002,
    }
    base.update(over)
    return Config.model_validate(base)


def mock_client(
    handler: Callable[[httpx.Request], httpx.Response],
    config: Config | None = None,
    *,
    before_attempt: BeforeAttempt | None = None,
    clock: Clock | None = None,
) -> Client:
    """Клиент поверх httpx.MockTransport с base_url http://svc."""
    conf = config if config is not None else cfg()
    if not conf.base_url:
        conf = conf.model_copy(update={"base_url": "http://svc"})
    return Client(
        conf,
        transport=httpx.MockTransport(handler),
        before_attempt=before_attempt,
        clock=clock,
    )


@dataclass
class Received:
    method: str
    target: str
    headers: dict[str, str]
    body: bytes


@dataclass
class Server:
    url: str
    host: str
    received: list[Received] = field(default_factory=list)

    def count(self) -> int:
        return len(self.received)


Handler = Callable[[Received, asyncio.StreamWriter], Awaitable[None]]


async def respond(
    writer: asyncio.StreamWriter,
    status: int = 200,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes = b"",
) -> None:
    """Пишет один HTTP/1.1-ответ и закрывает соединение."""
    lines = [f"HTTP/1.1 {status} X", f"content-length: {len(body)}", "connection: close"]
    lines += [f"{name}: {value}" for name, value in (headers or {}).items()]
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
    await writer.drain()


def status_handler(status: int, body: bytes = b"") -> Handler:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await respond(writer, status, body=body)

    return handler


async def start_events(
    writer: asyncio.StreamWriter, content_type: str = "text/event-stream"
) -> None:
    """Открывает chunked-ответ SSE: события дописываются `send_event`."""
    head = f"HTTP/1.1 200 X\r\ncontent-type: {content_type}\r\ntransfer-encoding: chunked\r\n\r\n"
    writer.write(head.encode("latin-1"))
    await writer.drain()


async def send_chunk(writer: asyncio.StreamWriter, data: bytes) -> None:
    """Пишет один chunk тела и флашит его в сокет."""
    writer.write(f"{len(data):x}\r\n".encode("latin-1") + data + b"\r\n")
    await writer.drain()


async def end_chunks(writer: asyncio.StreamWriter) -> None:
    """Закрывает chunked-тело нулевым chunk'ом."""
    writer.write(b"0\r\n\r\n")
    await writer.drain()


@asynccontextmanager
async def serve(handler: Handler) -> AsyncIterator[Server]:
    """Локальный HTTP/1.1-сервер на 127.0.0.1: тесты идут через настоящий сокет."""
    connections: set[asyncio.Task[None]] = set()
    server_state: dict[str, Server] = {}

    async def on_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            connections.add(task)
        try:
            request = await _read_request(reader)
            if request is None:
                return
            server_state["server"].received.append(request)
            await handler(request, writer)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            if task is not None:
                connections.discard(task)
            with contextlib.suppress(Exception):
                writer.close()

    tcp = await asyncio.start_server(on_connect, "127.0.0.1", 0)
    port = tcp.sockets[0].getsockname()[1]
    server = Server(url=f"http://127.0.0.1:{port}", host=f"127.0.0.1:{port}")
    server_state["server"] = server
    try:
        yield server
    finally:
        tcp.close()
        for task in list(connections):
            task.cancel()
        with contextlib.suppress(Exception):
            await tcp.wait_closed()


async def _read_request(reader: asyncio.StreamReader) -> Received | None:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, ConnectionResetError):
        return None
    lines = head.decode("latin-1").split("\r\n")
    method, target, _ = lines[0].split(" ", 2)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    body = b""
    length = int(headers.get("content-length", "0"))
    if length:
        body = await reader.readexactly(length)
    return Received(method=method, target=target, headers=headers, body=body)
