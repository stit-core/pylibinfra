"""BotRunner: выбор режима, lifecycle HTTP-сервера, вебхук и остановка."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import Message
from dishka import Provider, Scope, make_async_container, provide
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel, SecretStr

from pylibinfra import httpkit
from pylibinfra.logkit import nop
from pylibinfra.testkit.bottest import MemoryRedis
from pylibinfra.testkit.logtest import Capture
from pylibinfra.tgbotkit import (
    SECRET_HEADER,
    PollingRunner,
    RunnerError,
    WebhookRunner,
    build_runner,
    default_error_ux,
    new_instruments,
)
from pylibinfra.tgbotkit.middleware import install_standard_middleware

from .conftest import TOKEN, cfg

SECRET = "s3cret-token"
UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 1,
        "date": 1700000000,
        "chat": {"id": 10, "type": "private"},
        "from": {"id": 10, "is_bot": False, "first_name": "T"},
        "text": "hi",
    },
}


class FakeBot(Bot):
    """Настоящий `Bot` (его требует диспетчер) с перехваченными вызовами вебхука."""

    def __init__(self) -> None:
        super().__init__(TOKEN)
        self.webhooks: list[dict[str, Any]] = []
        self.deleted = 0

    async def set_webhook(self, url: str, **kwargs: Any) -> bool:  # type: ignore[override]
        self.webhooks.append({"url": url, **kwargs})
        return True

    async def delete_webhook(self, **kwargs: Any) -> bool:  # type: ignore[override]
        self.deleted += 1
        return True


def _server(log: Capture | None = None) -> httpkit.Server:
    return httpkit.Server(httpkit.Config(addr=":0"), log, autostart=False)


def _runner(
    bot_config: Any = None,
    *,
    bot: Bot | None = None,
    server: httpkit.Server | None = None,
    log: Capture | None = None,
    dp: Dispatcher | None = None,
    **extra: Any,
) -> Any:
    return build_runner(
        cfg=bot_config or cfg(),
        bot=bot or FakeBot(),
        dp=dp if dp is not None else Dispatcher(),
        container=make_async_container(),
        client=MemoryRedis(),  # type: ignore[arg-type]
        server=server or _server(log),
        log=log,
        **extra,
    )


async def _wait_listening(server: httpkit.Server) -> str:
    for _ in range(200):
        if server.addr():
            return f"http://{server.addr()}"
        await asyncio.sleep(0.01)
    raise AssertionError("server did not listen")


def test_build_runner_selects_polling_by_default() -> None:
    assert isinstance(_runner(), PollingRunner)


def test_build_runner_selects_webhook_mode() -> None:
    config = cfg(
        mode="webhook",
        webhook_base_url="https://bot.example",
        webhook_secret=SecretStr(SECRET),
    )
    assert isinstance(_runner(config), WebhookRunner)


async def test_run_rejects_autostarted_server() -> None:
    server = httpkit.Server(httpkit.Config(addr=":0"), autostart=True)
    with pytest.raises(RunnerError, match="autostart=False"):
        await _runner(server=server).run()


async def test_polling_serves_probes_and_stops_on_request() -> None:
    dp = Dispatcher()
    stopped = asyncio.Event()
    polls: list[dict[str, Any]] = []

    async def start_polling(*args: Any, **kwargs: Any) -> None:
        polls.append(kwargs)
        await stopped.wait()

    async def stop_polling() -> None:
        stopped.set()

    dp.start_polling = start_polling  # type: ignore[method-assign]
    dp.stop_polling = stop_polling  # type: ignore[method-assign]

    dp.message.register(lambda message: None)
    server = _server()
    runner = _runner(cfg(drop_pending_updates=True), dp=dp, server=server)
    task = asyncio.create_task(runner.run())
    try:
        base = await _wait_listening(server)
        async with httpx.AsyncClient(base_url=base) as client:
            assert (await client.get("/health")).status_code == 200
        # Сессию бота закрывает контейнер, а не диспетчер; типы апдейтов считаются
        # по фактическим обработчикам, а не хардкодятся.
        assert polls[0]["close_bot_session"] is False
        assert polls[0]["drop_pending_updates"] is True
        assert "message" in polls[0]["allowed_updates"]
        assert "edited_message" not in polls[0]["allowed_updates"]
    finally:
        runner.stop()
        await asyncio.wait_for(task, timeout=10)
    assert server.addr() == ""


async def test_polling_stops_when_dispatcher_returns() -> None:
    dp = Dispatcher()

    async def start_polling(*args: Any, **kwargs: Any) -> None:
        return None

    dp.start_polling = start_polling  # type: ignore[method-assign]
    server = _server()
    await _runner(dp=dp, server=server).run()
    assert server.addr() == ""


async def test_polling_propagates_dispatcher_failure() -> None:
    dp = Dispatcher()

    async def start_polling(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("getUpdates conflict")

    dp.start_polling = start_polling  # type: ignore[method-assign]
    server = _server()
    with pytest.raises(RuntimeError, match="conflict"):
        await _runner(dp=dp, server=server).run()
    assert server.addr() == ""


def _webhook_config(**overrides: Any) -> Any:
    return cfg(
        mode="webhook",
        webhook_base_url="https://bot.example",
        webhook_secret=SecretStr(SECRET),
        webhook_path="/tg/hook",
        **overrides,
    )


async def test_webhook_accepts_signed_updates_and_rejects_others() -> None:
    log = Capture()
    bot = FakeBot()
    dp = Dispatcher()
    dp.message.register(lambda message: None)
    server = _server(log)
    runner = _runner(
        _webhook_config(drop_pending_updates=True), bot=bot, dp=dp, server=server, log=log
    )
    task = asyncio.create_task(runner.run())
    try:
        base = await _wait_listening(server)
        async with httpx.AsyncClient(base_url=base) as client:
            assert (await client.post("/tg/hook", json=UPDATE)).status_code == 403
            wrong = await client.post(
                "/tg/hook", json=UPDATE, headers={SECRET_HEADER: SECRET + "x"}
            )
            assert wrong.status_code == 403
            signed = await client.post("/tg/hook", json=UPDATE, headers={SECRET_HEADER: SECRET})
            assert signed.status_code == 200

        assert bot.webhooks[0]["url"] == "https://bot.example/tg/hook"
        assert bot.webhooks[0]["secret_token"] == SECRET
        assert bot.webhooks[0]["drop_pending_updates"] is True
        assert "message" in bot.webhooks[0]["allowed_updates"]
    finally:
        runner.stop()
        await asyncio.wait_for(task, timeout=10)
        await bot.session.close()
    assert bot.deleted == 1


async def test_webhook_drops_undecodable_update() -> None:
    log = Capture()
    bot = FakeBot()
    server = _server(log)
    runner = _runner(_webhook_config(), bot=bot, server=server, log=log)
    task = asyncio.create_task(runner.run())
    try:
        base = await _wait_listening(server)
        async with httpx.AsyncClient(base_url=base) as client:
            response = await client.post(
                "/tg/hook", json={"nonsense": True}, headers={SECRET_HEADER: SECRET}
            )
        assert response.status_code == 200
        assert log.has("tgbotkit: undecodable update dropped", level="warn")
    finally:
        runner.stop()
        await asyncio.wait_for(task, timeout=10)
        await bot.session.close()


async def test_webhook_stops_the_server_when_set_webhook_fails() -> None:
    class _BrokenBot(FakeBot):
        async def set_webhook(self, url: str, **kwargs: Any) -> bool:  # type: ignore[override]
            raise RuntimeError("telegram is unreachable")

    bot = _BrokenBot()
    server = _server()
    runner = _runner(_webhook_config(), bot=bot, server=server)

    with pytest.raises(RuntimeError, match="unreachable"):
        await runner.run()
    await bot.session.close()

    assert server.addr() == ""
    # Чужой вебхук снимать нельзя: соседняя реплика продолжает принимать апдейты.
    assert bot.deleted == 0


async def test_webhook_is_removed_before_the_server_stops() -> None:
    order: list[str] = []

    class _OrderedBot(FakeBot):
        async def delete_webhook(self, **kwargs: Any) -> bool:  # type: ignore[override]
            order.append("delete_webhook")
            return await super().delete_webhook(**kwargs)

    bot = _OrderedBot()
    server = _server()
    runner = _runner(_webhook_config(), bot=bot, server=server)
    original_stop = server.stop

    async def stop() -> None:
        order.append("server_stop")
        await original_stop()

    server.stop = stop  # type: ignore[method-assign]

    task = asyncio.create_task(runner.run())
    try:
        await _wait_listening(server)
    finally:
        runner.stop()
        await asyncio.wait_for(task, timeout=10)
        await bot.session.close()

    # Сначала Telegram перестаёт слать апдейты, потом порт начинает отказывать.
    assert order == ["delete_webhook", "server_stop"]


async def test_webhook_emits_shutdown_after_a_failed_startup_hook() -> None:
    events: list[str] = []
    dp = Dispatcher()

    @dp.startup()
    async def _boom(**kwargs: Any) -> None:
        events.append("startup")
        raise RuntimeError("startup hook exploded")

    @dp.shutdown()
    async def _bye(**kwargs: Any) -> None:
        events.append("shutdown")

    bot = FakeBot()
    server = _server()
    runner = _runner(_webhook_config(), bot=bot, dp=dp, server=server)

    with pytest.raises(RuntimeError, match="startup hook exploded"):
        await runner.run()
    await bot.session.close()

    # Сорванный startup всё равно получает свой shutdown: поднятое наполовину
    # иначе не разбирает никто.
    assert events == ["startup", "shutdown"]
    assert bot.webhooks == []
    assert server.addr() == ""


async def test_webhook_runs_a_method_returned_by_the_handler() -> None:
    calls: list[Any] = []

    class _RecordingBot(FakeBot):
        async def __call__(self, method: Any, request_timeout: int | None = None) -> Any:
            calls.append(method)
            return None

    async def handler(message: Message) -> Any:
        return SendMessage(chat_id=message.chat.id, text="pong")

    dp = Dispatcher()
    dp.message.register(handler)
    bot = _RecordingBot()
    server = _server()
    runner = _runner(_webhook_config(), bot=bot, dp=dp, server=server)

    task = asyncio.create_task(runner.run())
    try:
        base = await _wait_listening(server)
        async with httpx.AsyncClient(base_url=base) as client:
            response = await client.post("/tg/hook", json=UPDATE, headers={SECRET_HEADER: SECRET})
        assert response.status_code == 200
        assert [type(m).__name__ for m in calls] == ["SendMessage"]
    finally:
        runner.stop()
        await asyncio.wait_for(task, timeout=10)
        await bot.session.close()


async def test_webhook_drops_a_body_that_is_not_json() -> None:
    log = Capture()
    bot = FakeBot()
    server = _server(log)
    runner = _runner(_webhook_config(), bot=bot, server=server, log=log)

    task = asyncio.create_task(runner.run())
    try:
        base = await _wait_listening(server)
        async with httpx.AsyncClient(base_url=base) as client:
            response = await client.post(
                "/tg/hook",
                content=b"not json at all",
                headers={SECRET_HEADER: SECRET, "Content-Type": "application/json"},
            )
        assert response.status_code == 200
        assert log.has("tgbotkit: undecodable webhook body dropped", level="warn")
    finally:
        runner.stop()
        await asyncio.wait_for(task, timeout=10)
        await bot.session.close()


def test_decode_error_reports_place_and_kind_without_values() -> None:
    from pydantic import ValidationError

    from pylibinfra.tgbotkit.runner.webhook import _decode_error

    class _Model(BaseModel):
        update_id: int

    try:
        _Model.model_validate({"update_id": "secret-token-value"})
    except ValidationError as exc:
        rendered = _decode_error(exc)
    # Тело апдейта в лог не едет: только путь поля и тип ошибки.
    assert rendered == "update_id: int_parsing"
    assert "secret-token-value" not in rendered


def test_decode_error_survives_an_empty_error_list() -> None:
    from pydantic import ValidationError

    from pylibinfra.tgbotkit.runner.webhook import _decode_error

    empty = ValidationError.from_exception_data("Update", [])
    assert _decode_error(empty) == "update: invalid"


async def test_webhook_update_span_continues_the_http_trace(
    span_exporter: InMemorySpanExporter,
) -> None:
    bot = FakeBot()
    dp = Dispatcher()
    install_standard_middleware(
        dp, _webhook_config(), nop(), new_instruments(), MemoryRedis(), default_error_ux()
    )
    server = _server()
    runner = _runner(_webhook_config(), bot=bot, dp=dp, server=server)

    task = asyncio.create_task(runner.run())
    try:
        base = await _wait_listening(server)
        async with httpx.AsyncClient(base_url=base) as client:
            await client.post("/tg/hook", json=UPDATE, headers={SECRET_HEADER: SECRET})
    finally:
        runner.stop()
        await asyncio.wait_for(task, timeout=10)
        await bot.session.close()

    spans = {span.name: span for span in span_exporter.get_finished_spans()}
    update = spans["tg.update"]
    server_span = spans["POST /tg/hook"]
    assert update.parent is not None
    assert update.parent.span_id == server_span.context.span_id  # type: ignore[union-attr]
    assert update.context.trace_id == server_span.context.trace_id  # type: ignore[union-attr]


async def test_webhook_route_is_hidden_from_openapi() -> None:
    bot = FakeBot()
    server = _server()
    runner = _runner(_webhook_config(), bot=bot, server=server)
    task = asyncio.create_task(runner.run())
    try:
        base = await _wait_listening(server)
        async with httpx.AsyncClient(base_url=base) as client:
            schema = (await client.get("/openapi.json")).json()
        assert "/tg/hook" not in schema["paths"]
    finally:
        runner.stop()
        await asyncio.wait_for(task, timeout=10)
        await bot.session.close()


class _FakeSubscription:
    def __init__(self) -> None:
        self.drained: list[float | None] = []

    async def unsubscribe(self) -> None: ...

    async def drain(self, *, timeout: float | None = None) -> None:  # noqa: ASYNC109
        self.drained.append(timeout)


class _Notifier:
    def __init__(self, subscription: _FakeSubscription) -> None:
        self.bus: Any = None
        self._subscription = subscription

    async def subscribe(self, bus: Any, delivery: Any) -> list[_FakeSubscription]:
        self.bus = bus
        return [self._subscription]


async def test_notifications_start_and_drain_with_the_runner() -> None:
    dp = Dispatcher()

    async def start_polling(*args: Any, **kwargs: Any) -> None:
        return None

    dp.start_polling = start_polling  # type: ignore[method-assign]
    subscription = _FakeSubscription()
    notifier = _Notifier(subscription)
    bus = object()

    runner = _runner(
        cfg(shutdown_timeout=3.0),
        dp=dp,
        notifiers=(notifier,),
        bus=bus,
    )
    await runner.run()
    assert notifier.bus is bus
    budget = subscription.drained[0]
    assert budget is not None
    assert 0.0 < budget <= 3.0  # BOT_SHUTDOWN_TIMEOUT уезжает в дренаж как общий бюджет


async def test_notifiers_without_bus_are_skipped_with_a_warning() -> None:
    dp = Dispatcher()

    async def start_polling(*args: Any, **kwargs: Any) -> None:
        return None

    dp.start_polling = start_polling  # type: ignore[method-assign]
    log = Capture()
    notifier = _Notifier(_FakeSubscription())

    await _runner(dp=dp, log=log, notifiers=(notifier,)).run()
    assert notifier.bus is None
    assert log.has(
        "tgbotkit: notifiers registered without a StreamBus, skipping subscriptions", level="warn"
    )


async def test_failed_notifier_start_still_closes_the_container() -> None:
    class _Broken:
        async def subscribe(self, bus: Any, delivery: Any) -> list[Any]:
            raise RuntimeError("stream is gone")

    closed: list[str] = []

    class _Tracked(Provider):
        scope = Scope.APP

        @provide
        async def resource(self) -> AsyncIterator[str]:
            yield "open"
            closed.append("closed")

    container = make_async_container(_Tracked())
    await container.get(str)
    server = _server()
    runner = build_runner(
        cfg=cfg(),
        bot=FakeBot(),
        dp=Dispatcher(),
        container=container,
        client=MemoryRedis(),  # type: ignore[arg-type]
        server=server,
        notifiers=(_Broken(),),
        bus=object(),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="stream is gone"):
        await runner.run()
    assert closed == ["closed"]
    assert server.addr() == ""


async def test_run_is_single_use() -> None:
    dp = Dispatcher()

    async def start_polling(*args: Any, **kwargs: Any) -> None:
        return None

    dp.start_polling = start_polling  # type: ignore[method-assign]
    runner = _runner(dp=dp)
    await runner.run()
    with pytest.raises(RunnerError, match="single-use"):
        await runner.run()


async def test_shutdown_survives_a_failing_container_close() -> None:
    class _Broken(Provider):
        scope = Scope.APP

        @provide
        async def resource(self) -> AsyncIterator[str]:
            yield "open"
            raise RuntimeError("cleanup exploded")

    dp = Dispatcher()

    async def start_polling(*args: Any, **kwargs: Any) -> None:
        return None

    dp.start_polling = start_polling  # type: ignore[method-assign]
    container = make_async_container(_Broken())
    await container.get(str)
    log = Capture()
    server = _server(log)
    runner = build_runner(
        cfg=cfg(),
        bot=FakeBot(),
        dp=dp,
        container=container,
        client=MemoryRedis(),  # type: ignore[arg-type]
        server=server,
        log=log,
    )

    await runner.run()
    entry = log.find("tgbotkit: shutdown step failed", level="error")
    assert entry is not None
    assert entry.str("step") == "container"


async def test_polling_stop_gives_up_after_the_deadline() -> None:
    dp = Dispatcher()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def start_polling(*args: Any, **kwargs: Any) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def stop_polling() -> None:
        await asyncio.Event().wait()

    dp.start_polling = start_polling  # type: ignore[method-assign]
    dp.stop_polling = stop_polling  # type: ignore[method-assign]
    log = Capture()
    server = _server(log)
    runner = _runner(cfg(shutdown_timeout=0.05), dp=dp, server=server, log=log)

    task = asyncio.create_task(runner.run())
    await asyncio.wait_for(started.wait(), timeout=10)
    runner.stop()
    await asyncio.wait_for(task, timeout=10)

    assert log.has("tgbotkit: polling stop timed out, cancelling", level="warn")
    assert cancelled.is_set()
    assert server.addr() == ""


async def test_polling_shutdown_stops_the_port_even_if_cancel_fails() -> None:
    dp = Dispatcher()
    started = asyncio.Event()

    async def start_polling(*args: Any, **kwargs: Any) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Диспетчер, падающий на отмене, не имеет права оставить порт занятым.
            raise RuntimeError("cancel exploded") from None

    async def stop_polling() -> None:
        await asyncio.Event().wait()

    dp.start_polling = start_polling  # type: ignore[method-assign]
    dp.stop_polling = stop_polling  # type: ignore[method-assign]
    log = Capture()
    server = _server(log)
    runner = _runner(cfg(shutdown_timeout=0.05), dp=dp, server=server, log=log)

    task = asyncio.create_task(runner.run())
    await asyncio.wait_for(started.wait(), timeout=10)
    runner.stop()
    await asyncio.wait_for(task, timeout=10)

    entry = log.find("tgbotkit: shutdown step failed", level="error")
    assert entry is not None
    assert entry.str("step") == "polling"
    assert server.addr() == ""


async def test_polling_stop_before_the_dispatcher_started_is_not_an_error() -> None:
    dp = Dispatcher()
    cancelled = asyncio.Event()

    async def start_polling(*args: Any, **kwargs: Any) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def stop_polling() -> None:
        raise RuntimeError("Polling is not started")

    dp.start_polling = start_polling  # type: ignore[method-assign]
    dp.stop_polling = stop_polling  # type: ignore[method-assign]
    runner = _runner(cfg(shutdown_timeout=0.05), dp=dp)

    task = asyncio.create_task(runner.run())
    runner.stop()
    await asyncio.wait_for(task, timeout=10)
    assert cancelled.is_set()


async def test_base_runner_serve_is_abstract() -> None:
    from pylibinfra.tgbotkit.runner.base import _BaseRunner

    runner = _BaseRunner(
        cfg=cfg(),
        bot=FakeBot(),
        dp=Dispatcher(),
        container=make_async_container(),
        client=MemoryRedis(),  # type: ignore[arg-type]
        server=_server(),
    )
    with pytest.raises(NotImplementedError):
        await runner.run()
