"""SSE-поток: разбор `text/event-stream` по спеке WHATWG, без retry и цепи."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Self

import httpx
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer

from .errors import EventTooLargeError, HeaderTimeoutError, NotEventStreamError, StatusError
from .metrics import Instruments, host_label, record_response, span_attributes

# Сколько тела неуспешного ответа попадает в StatusError.
MAX_ERROR_BODY = 2048

MEDIA_TYPE = "text/event-stream"

# Потолок поля `retry:` — 2^63-1 наносекунд, максимум длительности в Go: значение,
# не влезающее во float, насыщается им, а не роняет разбор потока.
MAX_RETRY = (2**63 - 1) / 1e9

_CR = 0x0D
_LF = 0x0A


@dataclass(frozen=True, slots=True)
class Event:
    """Одно событие потока: `data` строк склеены через `\\n`, `id` живёт между событиями."""

    id: str
    event: str
    data: str


class SSEStream:
    """Активный SSE-поток: async-итератор событий; закрывается `aclose()` или `async with`."""

    def __init__(self, response: httpx.Response, recorder: _Recorder, limit: int) -> None:
        self._response = response
        self._recorder = recorder
        self._lines = _LineReader(limit)
        self._parser = _Parser(limit)
        self._iterator: AsyncIterator[Event] | None = None
        self._closed = False

    @property
    def last_event_id(self) -> str:
        """Последний виденный `id:` потока — Last-Event-ID для реконнекта вызывающего."""
        return self._parser.last_id

    @property
    def retry(self) -> float:
        """Интервал реконнекта из поля `retry:` в секундах; `0.0`, если сервер его не слал."""
        return self._parser.retry

    def __aiter__(self) -> AsyncIterator[Event]:
        """Итератор событий; повторный вызов отдаёт тот же — поток читается один раз."""
        if self._iterator is None:
            self._iterator = self._read()
        return self._iterator

    async def aclose(self) -> None:
        """Закрывает тело и завершает спан; идемпотентен."""
        if self._closed:
            return
        self._closed = True
        self._recorder.finish(self._response, None)
        await self._response.aclose()

    async def __aenter__(self) -> Self:
        """Контекст-менеджер: выход закрывает поток."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Закрывает поток на выходе из блока."""
        await self.aclose()

    async def _read(self) -> AsyncIterator[Event]:
        try:
            async for chunk in self._response.aiter_bytes():
                for line in self._lines.feed(chunk):
                    event = self._parser.feed(line)
                    if event is not None:
                        yield event
            for line in self._lines.flush():
                event = self._parser.feed(line)
                if event is not None:
                    yield event
        except GeneratorExit:
            # Потребитель бросил итератор: await здесь оборвал бы финализацию
            # генератора, поэтому только закрываем спан.
            self._closed = True
            self._recorder.finish(self._response, None)
            raise
        except BaseException as exc:
            if self._closed:
                return
            self._closed = True
            self._recorder.finish(None, exc)
            await self._response.aclose()
            raise
        self._closed = True
        self._recorder.finish(self._response, None)
        await self._response.aclose()


async def open_stream(
    http: httpx.AsyncClient,
    request: httpx.Request,
    *,
    tracer: Tracer,
    instr: Instruments,
    header_timeout: float,
    limit: int,
) -> SSEStream:
    """Открывает поток: таймаут ограничивает только соединение и ожидание заголовков."""
    span = tracer.start_span(
        f"httpclient.Stream {request.method}",
        kind=SpanKind.CLIENT,
        attributes=span_attributes(request),
    )
    recorder = _Recorder(instr, span, host_label(request), request.method, time.perf_counter())
    try:
        with trace.use_span(
            span, end_on_exit=False, record_exception=False, set_status_on_exception=False
        ):
            async with asyncio.timeout(header_timeout):
                response = await http.send(request, stream=True)
    except TimeoutError as exc:
        failure = HeaderTimeoutError("httpclient: sse dial: response headers timeout")
        recorder.finish(None, failure)
        raise failure from exc
    except BaseException as exc:
        recorder.finish(None, exc)
        raise
    rejection = await _rejection(response)
    if rejection is not None:
        await response.aclose()
        recorder.finish(response, rejection)
        raise rejection
    return SSEStream(response, recorder, limit)


async def _rejection(response: httpx.Response) -> Exception | None:
    if response.status_code >= 400:
        return StatusError(response.status_code, await _error_body(response))
    media = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if media != MEDIA_TYPE:
        content_type = response.headers.get("content-type", "")
        return NotEventStreamError(f"httpclient: not an event stream: {content_type!r}")
    return None


async def _error_body(response: httpx.Response) -> str:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) >= MAX_ERROR_BODY:
            break
    return bytes(body[:MAX_ERROR_BODY]).decode("utf-8", "replace").strip()


class _Recorder:
    """Финализатор потока: `http_client_request_duration_seconds{mode="sse"}` и спан."""

    def __init__(
        self, instr: Instruments, span: Span, host: str, method: str, start: float
    ) -> None:
        self._instr = instr
        self._span = span
        self._host = host
        self._method = method
        self._start = start
        self._done = False

    def finish(self, response: httpx.Response | None, error: BaseException | None) -> None:
        """Пишет длительность и закрывает спан; повторные вызовы игнорируются."""
        if self._done:
            return
        self._done = True
        # Оборвавшийся посреди тела поток — status="error": заголовки 200 уже
        # ничего не говорят о результате.
        status = str(response.status_code) if response is not None else "error"
        self._instr.request_duration.record(
            time.perf_counter() - self._start,
            {"host": self._host, "method": self._method, "status": status, "mode": "sse"},
        )
        if response is not None:
            record_response(self._span, response)
        if error is not None:
            self._span.record_exception(error)
            self._span.set_status(Status(StatusCode.ERROR, str(error)))
        self._span.end()


class _LineReader:
    """Режет байтовый поток на строки по CR, LF или CRLF — спека допускает любой."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        """Строки, целиком пришедшие с этим куском."""
        self._buf += chunk
        return self._drain(final=False)

    def flush(self) -> Iterator[bytes]:
        """Хвост без терминатора на конце потока."""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> Iterator[bytes]:
        while True:
            index = _terminator(self._buf)
            if index < 0:
                if final and self._buf:
                    tail = bytes(self._buf)
                    self._buf.clear()
                    yield tail
                    return
                self._check()
                return
            if self._buf[index] == _LF:
                step = index + 1
            elif index + 1 < len(self._buf):
                step = index + 2 if self._buf[index + 1] == _LF else index + 1
            elif final:
                step = index + 1
            else:
                # CR на границе буфера: пока не ясно, CR это или CRLF.
                self._check()
                return
            line = bytes(self._buf[:index])
            del self._buf[:step]
            yield line

    def _check(self) -> None:
        if len(self._buf) > self._limit:
            raise EventTooLargeError(f"httpclient: sse line over {self._limit} bytes")


def _millis(value: bytes) -> float:
    """Поле `retry:` в секундах; абсурдная величина насыщается, а не рвёт разбор потока."""
    try:
        return int(value) / 1000
    except OverflowError:
        return MAX_RETRY


def _terminator(buf: bytearray) -> int:
    cr = buf.find(_CR)
    lf = buf.find(_LF)
    if cr < 0:
        return lf
    if lf < 0:
        return cr
    return min(cr, lf)


class _Parser:
    """Автомат полей SSE: `data` склеиваются, тип сбрасывается вместе с буфером."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._data = bytearray()
        self._has_data = False
        self._type = ""
        self.last_id = ""
        self.retry = 0.0

    def feed(self, line: bytes) -> Event | None:
        """Скармливает строку без терминатора; событие возвращается на конце блока."""
        if not line:
            return self._dispatch()
        if line.startswith(b":"):
            return None
        field, separator, value = line.partition(b":")
        if separator and value.startswith(b" "):
            value = value[1:]
        name = field.decode("utf-8", "replace")
        if name == "data":
            self._append(value)
        elif name == "event":
            self._type = value.decode("utf-8", "replace")
        elif name == "id":
            if b"\x00" not in value:
                self.last_id = value.decode("utf-8", "replace")
        elif name == "retry" and value.isdigit():
            self.retry = _millis(value)

        return None

    def _append(self, value: bytes) -> None:
        if len(self._data) + len(value) + 1 > self._limit:
            raise EventTooLargeError(f"httpclient: sse event over {self._limit} bytes")
        if self._has_data:
            self._data += b"\n"
        self._data += value
        self._has_data = True

    def _dispatch(self) -> Event | None:
        if not self._has_data:
            # Пустой data-буфер сбрасывает и тип события (спека).
            self._type = ""
            return None
        event = Event(id=self.last_id, event=self._type, data=self._data.decode("utf-8", "replace"))
        self._data.clear()
        self._has_data = False
        self._type = ""
        return event
