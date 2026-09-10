"""Server — сборка grpc.aio-сервера, bind, старт и graceful-остановка."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NewType

import grpc
from opentelemetry.instrumentation.grpc import aio_server_interceptor

from pylibinfra.healthkit import Probes
from pylibinfra.logkit import Logger, nop

from .auth import Authenticator, AuthInterceptor
from .config import (
    DEFAULT_KEEPALIVE_MIN_TIME,
    DEFAULT_KEEPALIVE_TIME,
    DEFAULT_KEEPALIVE_TIMEOUT,
    DEFAULT_MAX_MSG_SIZE,
    Config,
    ConfigError,
)
from .health import OVERALL, Health
from .health import SERVICE_NAME as HEALTH_SERVICE_NAME
from .interceptors import AccessLogInterceptor, RecoveryInterceptor, RequestIDInterceptor
from .metrics import Instruments, new_instruments

# Консьюмер передаёт add_<Svc>Servicer_to_server как partial(servicer, ...): кит не
# знает сгенерированных типов, регистрацию делает сам сервис.
ServiceRegistrar = Callable[[Any], None]
Services = tuple[ServiceRegistrar, ...]
# Полные имена сервисов для reflection: grpc-python, в отличие от Go, не собирает
# их с сервера сам. NewType, а не голый алиас: `tuple[str, ...]` в контейнере
# консьюмера мог бы значить что угодно, и его Provider молча перекрыл бы этот.
ServiceNames = NewType("ServiceNames", tuple[str, ...])
# Предикат «трассировать ли вызов»: аргумент — handler-call-details otel-grpc.
TraceFilter = Callable[[Any], bool]

_UNIX_SCHEME = "unix:"


class ListenError(RuntimeError):
    """Занять адрес или UDS-сокет не удалось: падает на старте, а не на первом RPC."""


def build_interceptors(
    log: Logger,
    instruments: Instruments,
    *,
    authenticator: Authenticator | None = None,
    extra: Sequence[Any] = (),
    trace_filter: TraceFilter | None = None,
) -> list[Any]:
    """Цепочка снаружи внутрь: OTel → request-id → access-log → recovery → auth → консьюмер.

    `trace_filter` получает handler-call-details вызова; `False` — не трассировать
    его (метрики и access-log пишутся всё равно).
    """
    chain: list[Any] = [
        aio_server_interceptor(filter_=trace_filter),  # type: ignore[no-untyped-call]
        RequestIDInterceptor(),
        AccessLogInterceptor(log, instruments),
        RecoveryInterceptor(log, instruments),
    ]
    if authenticator is not None:
        chain.append(AuthInterceptor(authenticator, log))
    chain.extend(extra)
    return chain


class Server:
    """gRPC-сервер кита: транспорт, интерсепторы, health и их lifecycle."""

    def __init__(
        self,
        cfg: Config,
        log: Logger | None = None,
        *,
        services: Services = (),
        probes: Probes = (),
        authenticator: Authenticator | None = None,
        interceptors: Sequence[Any] = (),
        server_options: Sequence[tuple[str, Any]] = (),
        service_names: Sequence[str] = (),
        trace_filter: TraceFilter | None = None,
    ) -> None:
        self._cfg = cfg
        self._log = log if log is not None else nop()
        self._instruments = new_instruments()
        self._addr = ""
        self._bound_uds = ""
        self._listening = False
        self._server = grpc.aio.server(
            interceptors=build_interceptors(
                self._log,
                self._instruments,
                authenticator=authenticator,
                extra=interceptors,
                trace_filter=trace_filter,
            ),
            options=self._options(server_options),
        )
        self._health = Health(cfg, self._log, probes)
        self._health.register(self._server)
        for register in services:
            register(self._server)
        if cfg.reflection:
            _enable_reflection(self._server, service_names)

    def grpc(self) -> Any:
        """Нижележащий `grpc.aio.Server` — поздняя регистрация сервисов, интероп."""
        return self._server

    def addr(self) -> str:
        """Фактический TCP-адрес слушателя; пусто до `listen` и при выключенном TCP."""
        return self._addr

    async def set_serving(self, service: str, ok: bool) -> None:
        """Ручной health-статус сервиса (`grpckit.OVERALL` — весь сервер)."""
        await self._health.set_serving(service, ok)

    def listen(self) -> None:
        """Занимает UDS и/или TCP синхронно; занятый адрес — `ListenError`. Идемпотентен."""
        if self._listening:
            return
        credentials = self._credentials()
        if self._cfg.uds_path:
            unlink_stale(self._cfg.uds_path)
            self._bind(f"{_UNIX_SCHEME}{self._cfg.uds_path}", credentials)
            self._bound_uds = self._cfg.uds_path
        try:
            if self._cfg.addr:
                target = tcp_target(self._cfg.addr)
                port = self._bind(target, credentials)
                self._addr = f"{target.rpartition(':')[0]}:{port}"
        except ListenError:
            # Отказ второго bind оставил бы сокет, который никто не обслуживает:
            # клиент получал бы ECONNREFUSED вместо «адреса нет».
            self._release_uds()
            raise
        self._listening = True

    async def serve(self) -> None:
        """Занимает адреса (если ещё нет), стартует сервер и включает health-цикл."""
        self.listen()
        await self._server.start()
        await self._health.set_serving(OVERALL, True)
        self._health.start()
        self._log.info("grpckit: serving", addr=self._addr, uds=self._cfg.uds_path)

    async def stop(self) -> None:
        """Health → NOT_SERVING, затем graceful-остановка в пределах `GRPC_SHUTDOWN_GRACE`."""
        await self._health.stop()
        await self._server.stop(self._cfg.shutdown_grace)
        self._release_uds()
        self._listening = False
        self._log.info("grpckit: stopped")

    def _release_uds(self) -> None:
        # Снимаем только тот сокет, который заняли сами: иначе остановка сервера,
        # не дошедшего до bind, удалила бы файл живого соседа по тому же пути.
        path, self._bound_uds = self._bound_uds, ""
        if path:
            unlink_stale(path)

    def _bind(self, target: str, credentials: Any) -> int:
        try:
            if credentials is not None:
                return int(self._server.add_secure_port(target, credentials))
            return int(self._server.add_insecure_port(target))
        except RuntimeError as exc:
            raise ListenError(f"grpckit: listen {target}: {exc}") from exc

    def _credentials(self) -> Any:
        if not self._cfg.tls_cert_file:
            return None
        try:
            certificate = Path(self._cfg.tls_cert_file).read_bytes()
            key = Path(self._cfg.tls_key_file).read_bytes()
        except OSError as exc:
            raise ConfigError(f"grpckit: tls creds: {exc}") from exc
        return grpc.ssl_server_credentials(((key, certificate),))

    def _options(self, extra: Sequence[tuple[str, Any]]) -> list[tuple[str, Any]]:
        cfg = self._cfg
        # Опции консьюмера идут первыми: при дубле аргумента gRPC берёт первое
        # вхождение, поэтому одноимённая опция перекрывает опцию кита.
        options: list[tuple[str, Any]] = [
            *extra,
            # 0 в лимите сообщения означал бы «отбивать любое сообщение» — как в
            # golibinfra, нулевое значение читается как «дефолт».
            ("grpc.max_receive_message_length", cfg.max_recv_msg_size or DEFAULT_MAX_MSG_SIZE),
            ("grpc.max_send_message_length", cfg.max_send_msg_size or DEFAULT_MAX_MSG_SIZE),
            ("grpc.keepalive_time_ms", _millis(cfg.keepalive_time, DEFAULT_KEEPALIVE_TIME)),
            (
                "grpc.keepalive_timeout_ms",
                _millis(cfg.keepalive_timeout, DEFAULT_KEEPALIVE_TIMEOUT),
            ),
            (
                "grpc.http2.min_ping_interval_without_data_ms",
                _millis(cfg.keepalive_min_time, DEFAULT_KEEPALIVE_MIN_TIME),
            ),
            # Аргумент управляет отправкой ping'ов, а не их приёмом: без него grpc-core
            # молчит на простаивающем соединении, а grpc-go-сервер его пингует.
            ("grpc.keepalive_permit_without_calls", int(cfg.keepalive_permit_without_stream)),
        ]
        if cfg.max_concurrent_streams > 0:
            options.append(("grpc.max_concurrent_streams", cfg.max_concurrent_streams))
        return options


def tcp_target(addr: str) -> str:
    """Адрес для bind: голое `:9090` (нотация Go) grpc-python не принимает — это все интерфейсы."""
    return f"[::]{addr}" if addr.startswith(":") else addr


def unlink_stale(path: str) -> None:
    """Снимает старый UDS-файл: иначе bind падает EADDRINUSE на перезапуске."""
    with contextlib.suppress(OSError):
        Path(path).unlink(missing_ok=True)


def _millis(seconds: float, default: float) -> int:
    return int((seconds or default) * 1000)


def _enable_reflection(server: Any, service_names: Sequence[str]) -> None:
    try:
        # grpcio-reflection не в зависимостях pylibinfra, стабов у него тоже нет.
        from grpc_reflection.v1alpha import reflection
    except ImportError as exc:
        raise ConfigError(
            "grpckit: GRPC_REFLECTION=true needs the grpcio-reflection package, "
            "which pylibinfra does not depend on: add it to the service or unset the flag"
        ) from exc
    names = (*service_names, HEALTH_SERVICE_NAME, reflection.SERVICE_NAME)
    reflection.enable_server_reflection(names, server)
