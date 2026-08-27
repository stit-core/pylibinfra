"""httpclient — исходящий HTTP-клиент поверх httpx с retry (tenacity),
circuit breaker (purgatory), OTel-инструментированием (HTTPXClientInstrumentor +
span на запрос) и авто-прокидыванием X-Request-ID из contextvar.

Эмитит http_client_request_duration_seconds, http_client_breaker_transitions_total,
http_client_breaker_open, http_client_retry_attempts_total.
"""

import random
import time
from typing import Any, Self

import httpx
from opentelemetry import trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.trace import Status, StatusCode
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_base

from .breaker import new_breaker
from .config import Config
from .metrics import Instruments, new_instruments
from .reqid import HEADER_REQUEST_ID, request_id

# HTTPXClientInstrumentor патчит httpx глобально — однократно на процесс.
_instrumented = False


def _instrument_once() -> None:
    global _instrumented
    if not _instrumented:
        HTTPXClientInstrumentor().instrument()
        _instrumented = True


class _RetryableStatus(Exception):  # noqa: N818
    # 5xx как сигнал ретрая для tenacity; несёт ответ, чтобы вернуть последний.
    def __init__(self, response: httpx.Response) -> None:
        self.response = response


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, _RetryableStatus | httpx.TransportError)


class _Wait(wait_base):
    # Экспоненциальный backoff с джиттером, как backoff() в client.go:
    # base * 2**attempt + rand(0, base).
    def __init__(self, base: float) -> None:
        self.base = base

    def __call__(self, retry_state: RetryCallState) -> float:
        attempt: int = int(retry_state.attempt_number)
        exp: float = self.base * float(2**attempt)
        jitter: float = random.random() * self.base  # noqa: S311
        return exp + jitter


class Client:
    def __init__(self, cfg: Config, http: httpx.AsyncClient) -> None:
        _instrument_once()
        self._cfg = cfg
        self._http = http
        self._instr: Instruments = new_instruments()
        self._breaker = new_breaker(cfg.breaker, self._instr)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def request(
        self, method: str, url: str, *, retries: int | None = None, **kwargs: Any
    ) -> httpx.Response:
        headers = httpx.Headers(kwargs.pop("headers", None))
        rid = request_id.get()
        # Явно переданный заголовок приоритетнее contextvar (как RequestIDFrom в Go,
        # который не перезатирает уже установленный X-Request-ID).
        if rid and HEADER_REQUEST_ID not in headers:
            headers[HEADER_REQUEST_ID] = rid
        kwargs["headers"] = headers

        host = httpx.URL(url).host or self._http.base_url.host
        tracer = trace.get_tracer("app")
        with tracer.start_as_current_span(f"HTTP {method}") as span:
            span.set_attribute("http.request.method", method)
            span.set_attribute("server.address", host)

            status = "error"
            start = time.perf_counter()
            try:
                resp = await self._execute(method, url, retries=retries, **kwargs)
                status = str(resp.status_code)
                return resp
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            finally:
                self._instr.request_duration.record(
                    time.perf_counter() - start,
                    {"host": host, "method": method, "status": status},
                )

    async def _execute(
        self, method: str, url: str, *, retries: int | None, **kwargs: Any
    ) -> httpx.Response:
        # Брейкер оборачивает всю retry-последовательность (как
        # breaker.Execute(doWithRetry) в Go): открыт → OpenedState на входе,
        # быстрый фейл без обращения к transport.
        brk = await self._breaker.get_breaker(self._cfg.breaker.name)
        async with brk:
            return await self._do_with_retry(method, url, retries=retries, **kwargs)

    async def _do_with_retry(
        self, method: str, url: str, *, retries: int | None, **kwargs: Any
    ) -> httpx.Response:
        host = httpx.URL(url).host or self._http.base_url.host
        # Per-call бюджет (ApiClient гейтит мутации без Idempotency-Key нулём) поверх
        # конфигного дефолта.
        max_retries = self._cfg.max_retries if retries is None else retries

        def _count_retry(_: RetryCallState) -> None:
            self._instr.retry_attempts.add(1, {"host": host})

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max_retries + 1),
                wait=_Wait(self._cfg.retry_wait),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
                before_sleep=_count_retry,
            ):
                with attempt:
                    resp = await self._http.request(method, url, **kwargs)
                    if resp.status_code >= 500:
                        raise _RetryableStatus(resp)
                    return resp
        except _RetryableStatus as exc:
            # Исчерпали ретраи на 5xx: поднимаем как ошибку, чтобы брейкер засчитал
            # фейл (Go возвращает error «server error after N retries»).
            raise httpx.HTTPStatusError(
                f"httpclient: server error after {max_retries} retries",
                request=exc.response.request,
                response=exc.response,
            ) from None
        raise RuntimeError("httpclient: unreachable")  # pragma: no cover
