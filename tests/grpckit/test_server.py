import shutil
import socket
import subprocess
from pathlib import Path

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from pylibinfra.grpckit import Config, ConfigError, ListenError, Server, tcp_target
from pylibinfra.testkit.grpctest import TCP, running_server

from .conftest import echo_registrar, method, stub_reflection


async def test_echo_over_uds() -> None:
    async with (
        running_server(echo_registrar()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        assert await channel.unary_unary(method("Unary"))(b"ping") == b"ping"


async def test_echo_over_tcp_port_chosen_by_os() -> None:
    async with (
        running_server(echo_registrar(), transport=TCP) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        assert target.startswith("127.0.0.1:")
        assert not target.endswith(":0")
        assert await channel.unary_unary(method("Unary"))(b"ping") == b"ping"


async def test_addr_reports_the_bound_port() -> None:
    server = Server(Config.model_validate({"addr": "127.0.0.1:0"}))

    assert server.addr() == ""
    server.listen()
    host, _, port = server.addr().rpartition(":")

    assert host == "127.0.0.1"
    assert int(port) > 0

    await server.stop()


async def test_listen_is_idempotent() -> None:
    server = Server(Config.model_validate({"addr": "127.0.0.1:0"}))
    server.listen()
    bound = server.addr()

    server.listen()

    assert server.addr() == bound
    await server.stop()


async def test_busy_port_fails_on_listen() -> None:
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    port = holder.getsockname()[1]
    try:
        server = Server(Config.model_validate({"addr": f"127.0.0.1:{port}"}))

        with pytest.raises(ListenError, match=r"grpckit: listen 127\.0\.0\.1"):
            server.listen()
    finally:
        holder.close()


async def test_health_is_serving_after_serve() -> None:
    async with (
        running_server(echo_registrar()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        response = await health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest())

        assert response.status == health_pb2.HealthCheckResponse.SERVING


async def test_set_serving_controls_a_named_service() -> None:
    cfg = Config.model_validate({"addr": "127.0.0.1:0", "shutdown_grace": 1.0})
    server = Server(cfg)
    await server.serve()
    await server.set_serving("test.Echo", True)
    try:
        async with grpc.aio.insecure_channel(server.addr()) as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            serving = await stub.Check(health_pb2.HealthCheckRequest(service="test.Echo"))
            assert serving.status == health_pb2.HealthCheckResponse.SERVING

            await server.set_serving("test.Echo", False)
            down = await stub.Check(health_pb2.HealthCheckRequest(service="test.Echo"))
            assert down.status == health_pb2.HealthCheckResponse.NOT_SERVING
    finally:
        await server.stop()


async def test_grpc_exposes_underlying_server() -> None:
    server = Server(Config.model_validate({"addr": "127.0.0.1:0"}))

    assert hasattr(server.grpc(), "add_generic_rpc_handlers")

    await server.stop()


async def test_uds_socket_is_removed_on_stop(tmp_path: Path) -> None:
    sock = tmp_path / "grpckit.sock"
    cfg = Config.model_validate({"addr": "", "uds_path": str(sock), "shutdown_grace": 1.0})
    server = Server(cfg)
    await server.serve()

    assert sock.exists()

    await server.stop()
    assert not sock.exists()


async def test_stop_keeps_a_uds_the_server_never_bound(tmp_path: Path) -> None:
    # По тому же пути может слушать живой сосед: остановка сервера, не дошедшего
    # до bind, не должна сносить его сокет.
    foreign = tmp_path / "foreign.sock"
    foreign.write_bytes(b"")
    cfg = Config.model_validate({"addr": "", "uds_path": str(foreign), "shutdown_grace": 1.0})

    await Server(cfg).stop()

    assert foreign.exists()


async def test_failed_tcp_bind_releases_the_uds(tmp_path: Path) -> None:
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    port = holder.getsockname()[1]
    sock = tmp_path / "partial.sock"
    cfg = Config.model_validate(
        {"addr": f"127.0.0.1:{port}", "uds_path": str(sock), "shutdown_grace": 1.0}
    )
    server = Server(cfg)
    try:
        with pytest.raises(ListenError):
            server.listen()

        assert not sock.exists()
    finally:
        holder.close()
        await server.stop()


async def test_message_size_limit_from_config() -> None:
    cfg = Config.model_validate(
        {"addr": "127.0.0.1:0", "shutdown_grace": 1.0, "max_recv_msg_size": 16}
    )
    async with (
        running_server(echo_registrar(), config=cfg) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await channel.unary_unary(method("Unary"))(b"x" * 64)

    assert exc_info.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED


async def test_zero_message_size_means_default() -> None:
    cfg = Config.model_validate(
        {"addr": "127.0.0.1:0", "shutdown_grace": 1.0, "max_recv_msg_size": 0}
    )
    async with (
        running_server(echo_registrar(), config=cfg) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        assert await channel.unary_unary(method("Unary"))(b"x" * 64) == b"x" * 64


async def test_consumer_server_options_win() -> None:
    cfg = Config.model_validate({"addr": "127.0.0.1:0", "shutdown_grace": 1.0})
    server = Server(cfg, server_options=(("grpc.max_receive_message_length", 16),))
    echo_registrar()(server.grpc())
    await server.serve()
    try:
        async with grpc.aio.insecure_channel(server.addr()) as channel:
            with pytest.raises(grpc.aio.AioRpcError) as exc_info:
                await channel.unary_unary(method("Unary"))(b"x" * 64)
        assert exc_info.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    finally:
        await server.stop()


async def test_reflection_without_the_package_fails_fast() -> None:
    cfg = Config.model_validate({"addr": "127.0.0.1:0", "reflection": True})

    with pytest.raises(ConfigError, match="grpcio-reflection"):
        Server(cfg)


async def test_reflection_registers_service_names_plus_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, ...]] = []
    stub_reflection(monkeypatch, seen)
    cfg = Config.model_validate({"addr": "127.0.0.1:0", "reflection": True, "shutdown_grace": 1.0})

    server = Server(cfg, service_names=("test.Echo", "test.Greeter"))

    # grpc-python не собирает имена сервисов с сервера сам — их даёт консьюмер,
    # health и сам reflection кит добавляет.
    assert seen == [
        (
            "test.Echo",
            "test.Greeter",
            "grpc.health.v1.Health",
            "grpc.reflection.v1alpha.ServerReflection",
        )
    ]

    await server.stop()


async def test_reflection_without_service_names_still_exposes_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, ...]] = []
    stub_reflection(monkeypatch, seen)
    cfg = Config.model_validate({"addr": "127.0.0.1:0", "reflection": True, "shutdown_grace": 1.0})

    server = Server(cfg)

    assert seen == [("grpc.health.v1.Health", "grpc.reflection.v1alpha.ServerReflection")]

    await server.stop()


async def test_unreadable_tls_pair_fails_on_listen(tmp_path: Path) -> None:
    cfg = Config.model_validate(
        {
            "addr": "127.0.0.1:0",
            "tls_cert_file": str(tmp_path / "missing.pem"),
            "tls_key_file": str(tmp_path / "missing.key"),
        }
    )
    server = Server(cfg)

    with pytest.raises(ConfigError, match="grpckit: tls creds"):
        server.listen()

    await server.stop()


def _self_signed(tmp_path: Path) -> tuple[Path, Path]:
    cert, key = tmp_path / "server.pem", tmp_path / "server.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


@pytest.mark.skipif(
    shutil.which("openssl") is None, reason="нужен openssl для тестового сертификата"
)
async def test_tls_pair_serves_over_secure_channel(tmp_path: Path) -> None:
    cert, key = _self_signed(tmp_path)
    cfg = Config.model_validate(
        {
            "addr": "127.0.0.1:0",
            "shutdown_grace": 1.0,
            "tls_cert_file": str(cert),
            "tls_key_file": str(key),
        }
    )
    server = Server(cfg)
    echo_registrar()(server.grpc())
    await server.serve()
    credentials = grpc.ssl_channel_credentials(root_certificates=cert.read_bytes())
    try:
        async with grpc.aio.secure_channel(
            server.addr(),
            credentials,
            options=(("grpc.ssl_target_name_override", "localhost"),),
        ) as channel:
            assert await channel.unary_unary(method("Unary"))(b"ping") == b"ping"
    finally:
        await server.stop()


async def test_go_notation_addr_binds_all_interfaces() -> None:
    # `:0` — нотация golibinfra; grpc-python требует хост, кит подставляет его сам.
    server = Server(Config.model_validate({"addr": ":0", "shutdown_grace": 1.0}))
    echo_registrar()(server.grpc())
    await server.serve()
    try:
        host, _, port = server.addr().rpartition(":")
        assert host == "[::]"

        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            assert await channel.unary_unary(method("Unary"))(b"ping") == b"ping"
    finally:
        await server.stop()


def test_tcp_target_keeps_explicit_host() -> None:
    assert tcp_target("127.0.0.1:9090") == "127.0.0.1:9090"
    assert tcp_target(":9090") == "[::]:9090"


async def test_max_concurrent_streams_option_is_applied() -> None:
    cfg = Config.model_validate(
        {"addr": "127.0.0.1:0", "shutdown_grace": 1.0, "max_concurrent_streams": 2}
    )
    async with (
        running_server(echo_registrar(), config=cfg) as address,
        grpc.aio.insecure_channel(address) as channel,
    ):
        assert await channel.unary_unary(method("Unary"))(b"ping") == b"ping"
