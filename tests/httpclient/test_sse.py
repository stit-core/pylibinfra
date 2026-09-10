from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from pylibinfra._internal import requestid
from pylibinfra.httpclient import (
    Client,
    Event,
    EventTooLargeError,
    HeaderTimeoutError,
    NotEventStreamError,
    SSEStream,
    StatusError,
)
from pylibinfra.httpclient.sse import MAX_RETRY

from .conftest import (
    Handler,
    Received,
    Server,
    cfg,
    end_chunks,
    respond,
    send_chunk,
    serve,
    start_events,
)


def body_handler(body: str, content_type: str = "text/event-stream") -> Handler:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await respond(writer, 200, headers={"content-type": content_type}, body=body.encode())

    return handler


async def collect(stream: SSEStream) -> list[Event]:
    return [event async for event in stream]


async def open_stream(server: Server, config: object = None, **kwargs: object) -> SSEStream:
    client = Client(config if config is not None else cfg())  # type: ignore[arg-type]
    return await client.stream("GET", f"{server.url}/events", **kwargs)


async def test_parses_events_including_done_marker() -> None:
    async with serve(body_handler("data: hello\n\ndata: world\n\ndata: [DONE]\n\n")) as server:
        stream = await open_stream(server)
        events = await collect(stream)

    assert [e.data for e in events] == ["hello", "world", "[DONE]"]


async def test_fields_comments_and_multiline_data() -> None:
    body = ": ping\nevent: token\nid: 7\ndata: line1\ndata: line2\n\n"
    async with serve(body_handler(body)) as server:
        stream = await open_stream(server)
        events = await collect(stream)

    assert events == [Event(id="7", event="token", data="line1\nline2")]
    assert stream.last_event_id == "7"


async def test_block_without_data_resets_event_type() -> None:
    async with serve(body_handler("event: foo\n\ndata: bar\n\n")) as server:
        events = await collect(await open_stream(server))

    assert events == [Event(id="", event="", data="bar")]


async def test_id_persists_between_events() -> None:
    body = "id: 1\ndata: a\n\ndata: b\n\nid: 2\ndata: c\n\n"
    async with serve(body_handler(body)) as server:
        events = await collect(await open_stream(server))

    assert events == [
        Event(id="1", event="", data="a"),
        Event(id="1", event="", data="b"),
        Event(id="2", event="", data="c"),
    ]


async def test_id_with_nul_is_ignored() -> None:
    async with serve(body_handler("id: a\x00b\ndata: x\n\n")) as server:
        events = await collect(await open_stream(server))

    assert events[0].id == ""


async def test_retry_field_parsed_and_bad_value_ignored() -> None:
    async with serve(body_handler("retry: 1500\ndata: a\n\nretry: nope\ndata: b\n\n")) as server:
        stream = await open_stream(server)
        await collect(stream)

    assert stream.retry == 1.5


async def test_absurd_retry_value_stays_positive() -> None:
    async with serve(body_handler("retry: 18446744073709551615\ndata: hi\n\n")) as server:
        stream = await open_stream(server)
        events = await collect(stream)

    assert len(events) == 1
    assert stream.retry > 0


async def test_retry_beyond_float_range_saturates() -> None:
    # 400-значное число не влезает во float: разбор насыщается, а не роняет поток.
    async with serve(body_handler(f"retry: {'9' * 400}\ndata: hi\n\n")) as server:
        stream = await open_stream(server)
        events = await collect(stream)

    assert [e.data for e in events] == ["hi"]
    assert stream.retry == MAX_RETRY


async def test_all_line_terminators_accepted() -> None:
    body = "data: cr\r\rdata: crlf\r\n\r\ndata: lf\n\n"
    async with serve(body_handler(body)) as server:
        events = await collect(await open_stream(server))

    assert [e.data for e in events] == ["cr", "crlf", "lf"]


async def test_unterminated_tail_is_not_dispatched() -> None:
    async with serve(body_handler("data: full\n\ndata: partial")) as server:
        events = await collect(await open_stream(server))

    assert [e.data for e in events] == ["full"]


async def test_content_type_with_charset_accepted() -> None:
    handler = body_handler("data: ok\n\n", "text/event-stream; charset=utf-8")
    async with serve(handler) as server:
        events = await collect(await open_stream(server))

    assert [e.data for e in events] == ["ok"]


async def test_wrong_content_type_rejected() -> None:
    handler = body_handler("<html>maintenance</html>", "text/html")
    async with serve(handler) as server:
        with pytest.raises(NotEventStreamError, match="text/html"):
            await open_stream(server)


async def test_non_2xx_raises_status_error_with_body() -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await respond(writer, 404, body=b"model not found")

    async with serve(handler) as server:
        with pytest.raises(StatusError) as err:
            await open_stream(server)

    assert err.value.status == 404
    assert err.value.body == "model not found"


async def test_line_over_limit_fails() -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await start_events(writer)
        for _ in range(8):
            await send_chunk(writer, b"x" * 4096)
        await end_chunks(writer)

    async with serve(handler) as server:
        stream = await open_stream(server, cfg(sse_max_event_bytes=8192))
        with pytest.raises(EventTooLargeError, match="line over"):
            await collect(stream)


async def test_event_over_limit_fails() -> None:
    body = ("data: " + "y" * 1024 + "\n") * 16 + "\n"
    async with serve(body_handler(body)) as server:
        stream = await open_stream(server, cfg(sse_max_event_bytes=4096))
        with pytest.raises(EventTooLargeError, match="event over"):
            await collect(stream)


async def test_header_phase_timeout() -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(5)
        await respond(writer, 200, headers={"content-type": "text/event-stream"})

    async with serve(handler) as server:
        async with asyncio.timeout(2):
            with pytest.raises(HeaderTimeoutError):
                await open_stream(server, cfg(timeout=0.05))


async def test_body_outlives_the_timeout() -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await start_events(writer)
        await send_chunk(writer, b"data: first\n\n")
        await asyncio.sleep(0.2)
        await send_chunk(writer, b"data: late\n\n")
        await end_chunks(writer)

    async with serve(handler) as server:
        stream = await open_stream(server, cfg(timeout=0.05))
        events = await collect(stream)

    assert [e.data for e in events] == ["first", "late"]


async def test_aclose_stops_the_stream() -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await start_events(writer)
        for index in range(1000):
            await send_chunk(writer, f"data: {index}\n\n".encode())
            await asyncio.sleep(0.005)

    async with serve(handler) as server:
        stream = await open_stream(server)
        events = []
        async for event in stream:
            events.append(event)
            if len(events) == 1:
                await stream.aclose()

    assert len(events) == 1


async def test_async_with_closes_the_stream() -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await start_events(writer)
        await send_chunk(writer, b"data: one\n\n")
        await asyncio.sleep(5)

    async with serve(handler) as server, await open_stream(server) as stream:
        async for event in stream:
            assert event.data == "one"
            break


async def test_broken_stream_surfaces_the_error() -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await start_events(writer)
        await send_chunk(writer, b"data: first\n\n")
        writer.close()

    async with serve(handler) as server:
        stream = await open_stream(server)
        with pytest.raises(Exception, match=r"incomplete|closed|peer|chunk"):
            await collect(stream)


async def test_accept_header_defaults_to_event_stream() -> None:
    async with serve(body_handler("data: x\n\n")) as server:
        await collect(await open_stream(server))
        default_accept = server.received[0].headers["accept"]

        await collect(await open_stream(server, headers={"accept": "text/plain"}))

    assert default_accept == "text/event-stream"
    assert server.received[1].headers["accept"] == "text/plain"


async def test_request_id_is_forwarded_to_the_stream() -> None:
    async with serve(body_handler("data: x\n\n")) as server:
        token = requestid.bind("stream-req-id")
        try:
            await collect(await open_stream(server))
        finally:
            requestid.unbind(token)

    assert server.received[0].headers["x-request-id"] == "stream-req-id"


async def test_stream_is_not_retried_and_does_not_open_the_circuit() -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await respond(writer, 503, body=b"down")

    async with serve(handler) as server:
        config = cfg(max_retries=3, breaker={"failure_threshold": 2})
        async with Client(config) as client:
            for _ in range(3):
                with pytest.raises(StatusError):
                    await client.stream("GET", f"{server.url}/events")
            # Ни ретраев (3 вызова = 3 запроса), ни провалов цепи: обычный
            # запрос на тот же хост проходит.
            assert server.count() == 3
            assert (await client.get(f"{server.url}/ping")).status_code == 503


async def test_stream_metrics_and_span(
    read_metric: Callable[..., float], span_exporter: object
) -> None:
    async with serve(body_handler("data: x\n\n")) as server, Client(cfg()) as client:
        stream = await client.stream("GET", f"{server.url}/events")
        await collect(stream)

    assert (
        read_metric("http_client_request_duration_seconds", {"mode": "sse", "status": "200"}) == 1
    )
    names = [span.name for span in span_exporter.get_finished_spans()]  # type: ignore[attr-defined]
    assert "httpclient.Stream GET" in names


async def test_stream_dial_error_is_recorded(read_metric: Callable[..., float]) -> None:
    async with Client(cfg(base_url="", max_retries=0)) as client:
        with pytest.raises(Exception, match=r"Connect|connect"):
            await client.stream("GET", "http://127.0.0.1:1/events")

    assert (
        read_metric("http_client_request_duration_seconds", {"mode": "sse", "status": "error"}) == 1
    )


async def test_stream_status_error_is_recorded(read_metric: Callable[..., float]) -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await respond(writer, 404, body=b"nope")

    async with serve(handler) as server, Client(cfg()) as client:
        with pytest.raises(StatusError):
            await client.stream("GET", f"{server.url}/events")

    assert (
        read_metric("http_client_request_duration_seconds", {"mode": "sse", "status": "404"}) == 1
    )


async def test_bare_cr_at_end_of_stream_dispatches_the_event() -> None:
    async with serve(body_handler("data: x\r\r")) as server:
        events = await collect(await open_stream(server))

    assert [e.data for e in events] == ["x"]


async def test_cr_at_chunk_boundary_is_not_a_lost_line() -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await start_events(writer)
        await send_chunk(writer, b"data: a\r")
        await send_chunk(writer, b"\ndata: b\r\n\r\n")
        await end_chunks(writer)

    async with serve(handler) as server:
        events = await collect(await open_stream(server))

    assert [e.data for e in events] == ["a\nb"]


async def test_aclose_is_idempotent() -> None:
    async with serve(body_handler("data: x\n\n")) as server:
        stream = await open_stream(server)
        await collect(stream)
        await stream.aclose()
        await stream.aclose()


async def test_status_error_body_is_truncated() -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await start_events(writer, "text/plain")
        for _ in range(4):
            await send_chunk(writer, b"z" * 1024)
        await end_chunks(writer)

    async def status_handler_500(request: Received, writer: asyncio.StreamWriter) -> None:
        writer.write(
            b"HTTP/1.1 500 X\r\ncontent-type: text/plain\r\ntransfer-encoding: chunked\r\n\r\n"
        )
        await writer.drain()
        for _ in range(4):
            await send_chunk(writer, b"z" * 1024)
        await end_chunks(writer)

    async with serve(status_handler_500) as server:
        with pytest.raises(StatusError) as err:
            await open_stream(server)

    assert len(err.value.body) == 2048


def test_status_error_without_body_reads_cleanly() -> None:
    assert str(StatusError(503)) == "httpclient: sse: status 503"


async def test_repeated_aiter_returns_the_same_iterator() -> None:
    async with serve(body_handler("data: x\n\n")) as server:
        stream = await open_stream(server)

        assert stream.__aiter__() is stream.__aiter__()
        await collect(stream)


async def test_fields_without_space_and_without_colon() -> None:
    async with serve(body_handler("data:tight\n\ndata\n\n")) as server:
        events = await collect(await open_stream(server))

    assert [e.data for e in events] == ["tight", ""]
