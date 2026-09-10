"""Общее для тестов grpckit: логгер-перехватчик и generic-servicer без codegen."""

from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass, field
from typing import Any

import grpc
import pytest
from grpc_health.v1 import health_pb2

from pylibinfra.grpckit import ServiceRegistrar, identity, request_id

SERVICE = "test.Echo"


@dataclass
class Record:
    level: str
    msg: str
    fields: dict[str, Any]


@dataclass
class CaptureLogger:
    """`logkit.Logger`, копящий записи в памяти."""

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

    def calls(self) -> list[Record]:
        return [r for r in self.records if r.msg in {"grpc call", "grpc call failed"}]


@pytest.fixture
def log() -> CaptureLogger:
    return CaptureLogger()


async def _echo(request: bytes, context: Any) -> bytes:
    return request


async def _fail(request: bytes, context: Any) -> bytes:
    await context.abort(grpc.StatusCode.NOT_FOUND, "nope")
    return b""  # недостижимо


async def _down(request: bytes, context: Any) -> bytes:
    await context.abort(grpc.StatusCode.UNAVAILABLE, "backend down")
    return b""  # недостижимо


async def _boom(request: bytes, context: Any) -> bytes:
    raise ValueError("internal detail leaks nowhere")


async def _downstream(request: bytes, context: Any) -> bytes:
    """Handler, у которого сорвался исходящий gRPC-вызов и он его не обработал."""
    raise grpc.aio.AioRpcError(
        grpc.StatusCode.NOT_FOUND,
        grpc.aio.Metadata(),
        grpc.aio.Metadata(),
        details="user-42 missing in shard db-7",
    )


async def _own_metadata(request: bytes, context: Any) -> bytes:
    await context.send_initial_metadata((("x-own", "1"),))
    return request


async def _request_id(request: bytes, context: Any) -> bytes:
    return request_id().encode()


async def _whoami(request: bytes, context: Any) -> bytes:
    return repr(identity()).encode()


async def _sleep(request: bytes, context: Any) -> bytes:
    await asyncio.sleep(30)
    return request


async def _stream(request: bytes, context: Any) -> Any:
    yield b"1"
    yield b"2"


async def _stream_boom(request: bytes, context: Any) -> Any:
    yield b"1"
    raise ValueError("stream detail leaks nowhere")


async def _stream_fail(request: bytes, context: Any) -> Any:
    await context.abort(grpc.StatusCode.NOT_FOUND, "nope")
    yield b""  # недостижимо


async def _stream_empty(request: bytes, context: Any) -> Any:
    return
    yield b""  # делает функцию генератором


async def _collect(requests: Any, context: Any) -> bytes:
    return b"".join([message async for message in requests])


async def _pipe(requests: Any, context: Any) -> Any:
    async for message in requests:
        yield message


def registrar(handlers: dict[str, Any], service: str = SERVICE) -> ServiceRegistrar:
    """Регистратор generic-handler'ов: тест транспорта без сгенерированных стабов."""

    def register(server: Any) -> None:
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(service, handlers),))

    return register


def echo_registrar() -> ServiceRegistrar:
    """Сервис `test.Echo` со всеми исходами, которые проверяют тесты кита."""
    unary = grpc.unary_unary_rpc_method_handler
    stream = grpc.unary_stream_rpc_method_handler
    return registrar(
        {
            "Collect": grpc.stream_unary_rpc_method_handler(_collect),
            "Pipe": grpc.stream_stream_rpc_method_handler(_pipe),
            "StreamEmpty": stream(_stream_empty),
            "Unary": unary(_echo),
            "Fail": unary(_fail),
            "Down": unary(_down),
            "Boom": unary(_boom),
            "Downstream": unary(_downstream),
            "OwnMetadata": unary(_own_metadata),
            "RequestID": unary(_request_id),
            "Whoami": unary(_whoami),
            "Sleep": unary(_sleep),
            "Stream": stream(_stream),
            "StreamBoom": stream(_stream_boom),
            "StreamFail": stream(_stream_fail),
        }
    )


def method(name: str) -> str:
    """Полное имя метода тестового сервиса."""
    return f"/{SERVICE}/{name}"


async def wait_status(stub: Any, expected: int) -> int:
    """Ждёт заданный overall-статус health, опрашивая сервис ограниченное число раз."""
    status = -1
    for _ in range(400):
        response = await stub.Check(health_pb2.HealthCheckRequest())
        status = response.status
        if status == expected:
            return status
        await asyncio.sleep(0.005)
    return status


def stub_reflection(monkeypatch: pytest.MonkeyPatch, seen: list[tuple[str, ...]]) -> None:
    """Подменяет grpcio-reflection, которого нет в зависимостях pylibinfra."""
    reflection = types.ModuleType("grpc_reflection.v1alpha.reflection")
    reflection.SERVICE_NAME = "grpc.reflection.v1alpha.ServerReflection"  # type: ignore[attr-defined]
    reflection.enable_server_reflection = lambda names, server: seen.append(tuple(names))  # type: ignore[attr-defined]
    v1alpha = types.ModuleType("grpc_reflection.v1alpha")
    v1alpha.reflection = reflection  # type: ignore[attr-defined]
    package = types.ModuleType("grpc_reflection")
    package.v1alpha = v1alpha  # type: ignore[attr-defined]
    for name, module in (
        ("grpc_reflection", package),
        ("grpc_reflection.v1alpha", v1alpha),
        ("grpc_reflection.v1alpha.reflection", reflection),
    ):
        monkeypatch.setitem(sys.modules, name, module)
