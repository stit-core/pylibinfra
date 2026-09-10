"""HTTP-клиент на любое число upstream-хостов: retry, цепь на хост, редиректы, SSE."""

from __future__ import annotations

import asyncio
import random
import ssl
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

import httpx
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.trace import SpanKind

from pylibinfra._internal import otelkit, requestid
from pylibinfra.clockkit import Clock

from .breaker import Breakers, State
from .config import Config, new_config
from .errors import (
    BreakerOpenError,
    CrossHostRedirectError,
    TooManyRedirectsError,
)
from .metrics import Instruments, host_label, new_instruments, record_response, span_attributes
from .retryafter import parse_retry_after
from .sse import SSEStream, open_stream

KIT = "httpclient"

HEADER_IDEMPOTENCY_KEY = "Idempotency-Key"

# Потолок паузы из Retry-After сверх retry_max_wait: абсурдное значение сервера
# не должно превращаться в «повторим через год».
MAX_RETRY_AFTER = 300.0

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE", "TRACE"})

# Статусы, которые сервер отдаёт при перегрузке или временном отказе. 500 сюда
# не входит: у большинства upstream'ов он детерминирован.
RETRYABLE_STATUSES = frozenset({408, 429, 502, 503, 504})

# Статусы «хоста нет» для цепи: 500/501 от живого upstream'а её не размыкают.
BREAKER_FAILURE_STATUSES = frozenset({502, 503, 504})

# Единственные заголовки, которые переезжают на чужой хост: любой другой может
# оказаться секретом. Host/Connection/Transfer-Encoding httpx считает сам на хоп.
SAFE_REDIRECT_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "content-length",
        "content-type",
        "referer",
        "user-agent",
        requestid.HEADER.lower(),
        "host",
        "connection",
        "transfer-encoding",
    }
)

BeforeAttempt = Callable[[int], Awaitable[None]]

_allow_retry: ContextVar[bool] = ContextVar("pylibinfra_httpclient_allow_retry", default=False)


@contextmanager
def allow_retry() -> Iterator[None]:
    """Помечает вызовы внутри блока безопасными для повтора независимо от метода.

    Для POST/PATCH, идемпотентность которых обеспечена на стороне сервера.
    """
    token = _allow_retry.set(True)
    try:
        yield
    finally:
        _allow_retry.reset(token)


@dataclass(slots=True)
class _Outcome:
    """Признак «исход не про здоровье хоста»: отмена вызывающего, отказ hook'а."""

    client_side: bool = False


class Client:
    """Исходящий HTTP-клиент: retry, circuit breaker на хост, редиректы, SSE, OTel."""

    def __init__(
        self,
        cfg: Config,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        before_attempt: BeforeAttempt | None = None,
        headers: Mapping[str, str] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._cfg = cfg
        self._before = before_attempt
        self._instr: Instruments = new_instruments()
        self._breakers = Breakers(cfg.breaker, self._instr, clock)
        self._tracer = otelkit.tracer(KIT)
        self._http = httpx.AsyncClient(
            base_url=cfg.base_url,
            transport=transport,
            timeout=httpx.Timeout(cfg.timeout),
            headers=dict(headers) if headers else None,
            follow_redirects=False,
        )
        # Инструментируем свой клиент, а не httpx процесса: спаны попыток
        # получают только вызовы кита.
        HTTPXClientInstrumentor.instrument_client(self._http)

    @property
    def config(self) -> Config:
        """Конфиг, с которым клиент собран."""
        return self._cfg

    def breaker_state(self, host: str) -> State:
        """Состояние цепи `host[:port]`; хост без вызовов — `State.CLOSED`."""
        return self._breakers.state(host)

    async def aclose(self) -> None:
        """Снимает инструментацию и закрывает пул; клиент после этого непригоден."""
        HTTPXClientInstrumentor.uninstrument_client(self._http)
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        """Контекст-менеджер: выход закрывает клиент."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Закрывает клиент на выходе из блока."""
        await self.aclose()

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET; ретраится всегда (метод идемпотентен)."""
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """POST; без `allow_retry`/`Idempotency-Key` не ретраится."""
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        """PUT; ретраится (метод идемпотентен)."""
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        """PATCH; без `allow_retry`/`Idempotency-Key` не ретраится."""
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        """DELETE; ретраится (метод идемпотентен)."""
        return await self.request("DELETE", url, **kwargs)

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Запрос с retry и цепью хоста; `kwargs` — как у `httpx.AsyncClient.build_request`.

        Ответ с любым статусом, включая 5xx после исчерпания ретраев, возвращается
        как ответ; исключение — только транспорт, отмена вызывающего, нарушение
        политики редиректов и `BreakerOpenError`.
        """
        request = self._build(method, url, **kwargs)
        host = host_label(request)
        start = time.perf_counter()
        status = "error"
        with self._tracer.start_as_current_span(
            f"httpclient.Do {request.method}",
            kind=SpanKind.CLIENT,
            attributes=span_attributes(request),
        ) as span:
            try:
                response = await self._execute(request, host)
            except BreakerOpenError:
                status = "breaker_open"
                raise
            else:
                status = str(response.status_code)
                record_response(span, response)
                return response
            finally:
                self._instr.request_duration.record(
                    time.perf_counter() - start,
                    {"host": host, "method": request.method, "status": status},
                )

    async def stream(self, method: str, url: str, **kwargs: Any) -> SSEStream:
        """Открывает SSE-поток без retry и цепи: частично прочитанный поток неретраябелен.

        `Config.timeout` ограничивает только соединение и ожидание заголовков
        (`HeaderTimeoutError`); тело живёт до `aclose()` или отмены вызывающего.
        """
        headers = httpx.Headers(kwargs.pop("headers", None))
        if "accept" not in headers:
            headers["accept"] = "text/event-stream"
        kwargs["headers"] = headers
        # read=None: тело потока живёт дольше Timeout, ограничены только фаза
        # соединения и запись запроса.
        kwargs.setdefault(
            "timeout",
            httpx.Timeout(
                connect=self._cfg.timeout,
                read=None,
                write=self._cfg.timeout,
                pool=self._cfg.timeout,
            ),
        )
        request = self._build(method, url, **kwargs)
        return await open_stream(
            self._http,
            request,
            tracer=self._tracer,
            instr=self._instr,
            header_timeout=self._cfg.timeout,
            limit=self._cfg.sse_max_event_bytes,
        )

    def _build(self, method: str, url: str, **kwargs: Any) -> httpx.Request:
        request = self._http.build_request(method, url, **kwargs)
        current = requestid.current()
        # Явно переданный заголовок приоритетнее contextvar.
        if current and requestid.HEADER not in request.headers:
            request.headers[requestid.HEADER] = current
        return request

    async def _execute(self, request: httpx.Request, host: str) -> httpx.Response:
        guard = self._breakers.guard(host)
        outcome = _Outcome()
        try:
            response = await self._attempt(request, host, outcome)
        except asyncio.CancelledError:
            guard.skip()
            raise
        except Exception:
            if outcome.client_side:
                guard.skip()
            else:
                guard.failure()
            raise
        if response.status_code in BREAKER_FAILURE_STATUSES:
            guard.failure()
        else:
            guard.success()
        return response

    async def _attempt(
        self, request: httpx.Request, host: str, outcome: _Outcome
    ) -> httpx.Response:
        attempts = 1 + (self._cfg.max_retries if self._retryable(request) else 0)
        last_response: httpx.Response | None = None
        attempt = 0
        while True:
            final = attempt >= attempts - 1
            if attempt > 0:
                await asyncio.sleep(self._backoff(attempt, last_response))
                self._instr.retry_attempts.add(1, {"host": host})
            if self._before is not None:
                try:
                    await self._before(attempt)
                except Exception:
                    outcome.client_side = True
                    raise
            try:
                async with asyncio.timeout(self._cfg.timeout):
                    response = await self._send(request)
            except httpx.TransportError as exc:
                if final or permanent(exc):
                    raise
                last_response = None
            except TimeoutError:
                if final:
                    raise
                last_response = None
            else:
                if final or response.status_code not in RETRYABLE_STATUSES:
                    return response
                last_response = response
            attempt += 1

    async def _send(self, request: httpx.Request) -> httpx.Response:
        origin = request.url.netloc
        sent = 0
        current = request
        while True:
            response = await self._http.send(current)
            sent += 1
            follow = response.next_request
            if follow is None:
                return response
            await response.aclose()
            if sent >= self._cfg.max_redirects:
                raise TooManyRedirectsError(
                    f"httpclient: too many redirects: limit {self._cfg.max_redirects}"
                )
            if follow.url.netloc != origin:
                if not self._cfg.allow_cross_host_redirects:
                    raise CrossHostRedirectError(
                        "httpclient: cross-host redirect: "
                        f"{origin.decode()} -> {follow.url.netloc.decode()}"
                    )
                strip_unsafe_headers(follow.headers)
            current = follow

    def _retryable(self, request: httpx.Request) -> bool:
        if self._cfg.max_retries == 0 or not replayable(request):
            return False
        if (
            self._cfg.retry_non_idempotent
            or _allow_retry.get()
            or HEADER_IDEMPOTENCY_KEY in request.headers
        ):
            return True
        return request.method in IDEMPOTENT_METHODS

    def _backoff(self, attempt: int, last_response: httpx.Response | None) -> float:
        max_wait = self._cfg.retry_max_wait
        if last_response is not None:
            after = parse_retry_after(
                last_response.headers.get("Retry-After", ""), datetime.now(UTC)
            )
            if after is not None:
                return min(after, MAX_RETRY_AFTER, max_wait)
        base = self._cfg.retry_wait
        shift = attempt - 1
        # 2**shift на больших attempt переполняет float: сдвигаем, только пока
        # результат заведомо не выше потолка.
        exponential = max_wait if shift >= 62 else min(base * float(1 << shift), max_wait)
        jitter = random.random() * base  # noqa: S311 — джиттер backoff, не крипто
        if exponential > max_wait - jitter:
            return max_wait
        return exponential + jitter


def new(
    cfg: Config | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    before_attempt: BeforeAttempt | None = None,
    headers: Mapping[str, str] | None = None,
    clock: Clock | None = None,
) -> Client:
    """Клиент из конфига; без конфига — из `HTTPCLIENT_*` (`ConfigError` на невалидном)."""
    return Client(
        cfg if cfg is not None else new_config(),
        transport=transport,
        before_attempt=before_attempt,
        headers=headers,
        clock=clock,
    )


def replayable(request: httpx.Request) -> bool:
    """Тело можно отправить повторно: bytes/str/json/form — да, поток/генератор — нет."""
    try:
        _ = request.content
    except httpx.RequestNotRead:
        return False
    return True


def permanent(exc: BaseException) -> bool:
    """Результат детерминирован (схема URL, сертификат): повтор даст ту же ошибку."""
    if isinstance(exc, httpx.UnsupportedProtocol):
        return True
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ssl.SSLError):
            return True
        cause = cause.__cause__
    return False


def strip_unsafe_headers(headers: httpx.Headers) -> None:
    """Снимает с кросс-хостового редиректа всё, кроме allowlist'а безопасных заголовков."""
    for name in list(headers.keys()):
        if name.lower() not in SAFE_REDIRECT_HEADERS:
            del headers[name]
