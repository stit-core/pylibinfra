from collections.abc import Callable
from typing import Any

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from pylibinfra.grpckit import AUTHORIZATION_KEY, REQUEST_ID_KEY, identity
from pylibinfra.grpckit.auth import _is_exempt
from pylibinfra.testkit.grpctest import running_server

from .conftest import CaptureLogger, echo_registrar, method


class StubAuth:
    """Порт аутентификации теста: принимает один токен, остальное отвергает."""

    def __init__(self, token: str = "good") -> None:
        self.token = token
        self.seen: list[str] = []

    async def authenticate(self, token: str) -> Any:
        self.seen.append(token)
        if token != self.token:
            raise PermissionError("token 'secret-key-3' expired at 12:00")
        return {"sub": "u-1"}


async def test_calls_pass_without_authenticator() -> None:
    async with (
        running_server(echo_registrar()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        assert await channel.unary_unary(method("Unary"))(b"ping") == b"ping"
        assert await channel.unary_unary(method("Whoami"))(b"") == b"None"


async def test_missing_token_is_rejected_before_the_port() -> None:
    auth = StubAuth()
    async with (
        running_server(echo_registrar(), authenticator=auth) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        call = channel.unary_unary(method("Unary"))(b"ping")
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await call
        echoed = dict(await call.initial_metadata())

    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED
    assert exc_info.value.details() == "missing bearer token"
    assert auth.seen == []
    assert REQUEST_ID_KEY in echoed


@pytest.mark.parametrize("header", ["Bearer good", "bearer good", "BEARER  good ", "good"])
async def test_bearer_prefix_is_optional_and_case_insensitive(header: str) -> None:
    auth = StubAuth()
    async with (
        running_server(echo_registrar(), authenticator=auth) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        response = await channel.unary_unary(method("Unary"))(
            b"ping", metadata=((AUTHORIZATION_KEY, header),)
        )

    assert response == b"ping"
    assert auth.seen == ["good"]


async def test_bare_prefix_is_not_a_token() -> None:
    auth = StubAuth()
    async with (
        running_server(echo_registrar(), authenticator=auth) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await channel.unary_unary(method("Unary"))(
                b"ping", metadata=((AUTHORIZATION_KEY, "Bearer "),)
            )

    assert exc_info.value.details() == "missing bearer token"
    assert auth.seen == []


async def test_port_error_never_reaches_the_client(log: CaptureLogger) -> None:
    auth = StubAuth()
    async with (
        running_server(echo_registrar(), authenticator=auth, log=log) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await channel.unary_unary(method("Unary"))(
                b"ping", metadata=((AUTHORIZATION_KEY, "Bearer wrong"),)
            )

    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED
    assert exc_info.value.details() == "unauthenticated"
    assert "secret-key-3" not in exc_info.value.details()

    rejected = log.find("grpc auth rejected")
    assert rejected is not None
    assert rejected.level == "warn"
    assert rejected.fields["grpc_method"] == method("Unary")
    assert "secret-key-3" in rejected.fields["error"]


async def test_identity_is_visible_to_the_handler() -> None:
    async with (
        running_server(echo_registrar(), authenticator=StubAuth()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        who = await channel.unary_unary(method("Whoami"))(
            b"", metadata=((AUTHORIZATION_KEY, "Bearer good"),)
        )

    assert who == b"{'sub': 'u-1'}"
    assert identity() is None


async def test_health_is_exempt_from_auth() -> None:
    async with (
        running_server(echo_registrar(), authenticator=StubAuth()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        response = await health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest())

    assert response.status == health_pb2.HealthCheckResponse.SERVING


async def test_stream_requires_auth_too() -> None:
    auth = StubAuth()
    async with (
        running_server(echo_registrar(), authenticator=auth) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        call = channel.unary_stream(method("Stream"))(b"")

        async def drain() -> None:
            async for _ in call:
                pass

        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await drain()

        authorized = channel.unary_stream(method("Stream"))(
            b"", metadata=((AUTHORIZATION_KEY, "Bearer good"),)
        )
        assert [message async for message in authorized] == [b"1", b"2"]

    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED


async def test_rejected_call_is_counted_and_logged(read_metric: Callable[..., float]) -> None:
    async with (
        running_server(echo_registrar(), authenticator=StubAuth()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        with pytest.raises(grpc.aio.AioRpcError):
            await channel.unary_unary(method("Unary"))(b"ping")

    attrs = {"grpc_method": method("Unary"), "grpc_code": "UNAUTHENTICATED"}
    assert read_metric("grpc_server_requests_total", attrs) == 1
    assert read_metric("grpc_server_panics_total") == 0


@pytest.mark.parametrize(
    "exempt",
    [
        "/grpc.health.v1.Health/Check",
        "/grpc.health.v1.Health/Watch",
        "/grpc.reflection.v1.ServerReflection/ServerReflectionInfo",
        "/grpc.reflection.v1alpha.ServerReflection/ServerReflectionInfo",
    ],
)
def test_exempt_prefixes_match_golibinfra(exempt: str) -> None:
    # Reflection не в зависимостях pylibinfra, поэтому его exempt проверяется по имени.
    assert _is_exempt(exempt)
    assert not _is_exempt(method("Unary"))


async def test_every_rpc_shape_requires_auth() -> None:
    auth = StubAuth()

    async def messages() -> Any:
        yield b"a"

    async with (
        running_server(echo_registrar(), authenticator=auth) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        with pytest.raises(grpc.aio.AioRpcError) as unary:
            await channel.unary_unary(method("Unary"))(b"")

        with pytest.raises(grpc.aio.AioRpcError) as client_stream:
            await channel.stream_unary(method("Collect"))(messages())

        bidi = channel.stream_stream(method("Pipe"))(messages())
        with pytest.raises(grpc.aio.AioRpcError) as bidi_error:
            async for _ in bidi:
                pass

    for rejected in (unary, client_stream, bidi_error):
        assert rejected.value.code() == grpc.StatusCode.UNAUTHENTICATED
    assert auth.seen == []


async def test_identity_reaches_client_streaming_and_bidi_handlers() -> None:
    async def messages() -> Any:
        yield b"a"
        yield b"b"

    async with (
        running_server(echo_registrar(), authenticator=StubAuth()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        credentials = ((AUTHORIZATION_KEY, "Bearer good"),)
        assert await channel.stream_unary(method("Collect"))(messages(), metadata=credentials) == (
            b"ab"
        )
        piped = channel.stream_stream(method("Pipe"))(messages(), metadata=credentials)
        assert [message async for message in piped] == [b"a", b"b"]

    assert identity() is None


async def test_health_watch_stream_is_exempt_from_auth() -> None:
    async with (
        running_server(echo_registrar(), authenticator=StubAuth()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        watch = health_pb2_grpc.HealthStub(channel).Watch(health_pb2.HealthCheckRequest())
        first = await watch.read()
        watch.cancel()

    assert first.status == health_pb2.HealthCheckResponse.SERVING
