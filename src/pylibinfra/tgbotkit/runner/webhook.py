"""WebhookRunner — приём апдейтов вебхуком поверх сервера httpkit."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import Any

from aiogram.methods import TelegramMethod
from aiogram.types import Update
from fastapi import Depends, Request, Response
from pydantic import ValidationError

from pylibinfra import httpkit

from .base import _BaseRunner

# Имя заголовка, а не секрет: значение приходит из BOT_WEBHOOK_SECRET.
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105

_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class WebhookRunner(_BaseRunner):
    """Регистрирует POST-роут вебхука, ставит его в Telegram и держит сервер до остановки.

    Роут закрыт `shared_secret_guard` по `BOT_WEBHOOK_SECRET` — тот же секрет уезжает
    в `set_webhook`, поэтому чужой POST на путь вебхука получает `403`. Несколько
    инстансов безопасны: гонку за диалог снимает `RedisEventIsolation`.
    """

    def _register_route(self) -> None:
        guard = httpkit.shared_secret_guard(
            httpkit.SharedSecretConfig(header=SECRET_HEADER, secret=self._cfg.webhook_secret)
        )
        self._server.app.add_api_route(
            self._cfg.webhook_path,
            self._handle,
            methods=["POST"],
            dependencies=[Depends(guard)],
            include_in_schema=False,
        )

    async def _handle(self, request: Request) -> Response:
        try:
            payload = await request.json()
        except ValueError as exc:
            # Переотправка неразбираемого тела не поможет: подтверждаем и роняем.
            self._log.warn("tgbotkit: undecodable webhook body dropped", err=str(exc))
            return Response(status_code=200)
        try:
            update = Update.model_validate(payload, context={"bot": self._bot})
        except ValidationError as exc:
            self._log.warn("tgbotkit: undecodable update dropped", err=_decode_error(exc))
            return Response(status_code=200)
        result = await self._dp.feed_update(self._bot, update)
        if isinstance(result, TelegramMethod):
            # Обработчик вернул метод (штатная идиома aiogram): в polling его исполняет
            # сам диспетчер, здесь это обязан сделать runner, иначе ответ пропадёт.
            await self._dp.silent_call_request(bot=self._bot, result=result)
        return Response(status_code=200)

    async def _serve(self) -> None:
        self._register_route()
        http: asyncio.Task[None] | None = None
        installed = False
        try:
            # Внутри try: сорвавшийся startup-хук всё равно обязан получить свой
            # shutdown — иначе поднятое им наполовину не разбирается никем.
            await self._dp.emit_startup(**self._workflow_data())
            http = await self._serve_probes()
            await self._bot.set_webhook(
                self._cfg.webhook_url(),
                secret_token=self._cfg.webhook_secret.get_secret_value(),
                allowed_updates=self._dp.resolve_used_update_types(),
                drop_pending_updates=self._cfg.drop_pending_updates,
            )
            installed = True
            await self._wait_for_stop()
        finally:
            # Снимаем вебхук первым: Telegram перестаёт слать апдейты ещё до того,
            # как сервер начнёт отказывать в соединениях. Чужой вебхук не трогаем —
            # сорванный старт не имеет права оглушить работающую реплику.
            if installed:
                await self._step("delete_webhook", self._bot.delete_webhook())
            if http is not None:
                await self._step("http", self._stop_http(http))
            await self._step("emit_shutdown", self._dp.emit_shutdown(**self._workflow_data()))

    def _workflow_data(self) -> dict[str, Any]:
        """Тот же набор данных, что диспетчер раздаёт хукам в polling-режиме."""
        data = {k: v for k, v in self._dp.workflow_data.items() if k != "bot"}
        return {"bot": self._bot, "dispatcher": self._dp, "bots": (self._bot,), **data}

    async def _wait_for_stop(self) -> None:
        """Ждёт `stop()` или сигнала; обработчики снимаются, чтобы не пережить runner."""
        loop = asyncio.get_running_loop()
        installed = []
        for sig in _SIGNALS:
            # Вне главного потока и не на POSIX обработчики сигналов недоступны.
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.add_signal_handler(sig, self._stopped.set)
                installed.append(sig)
        try:
            await self._stopped.wait()
        finally:
            for sig in installed:
                with contextlib.suppress(NotImplementedError, ValueError):
                    loop.remove_signal_handler(sig)


def _decode_error(exc: ValidationError) -> str:
    """Место и тип первой ошибки разбора без значений полей: тело апдейта в лог не едет."""
    errors = exc.errors()
    if not errors:
        return "update: invalid"
    location = ".".join(str(part) for part in errors[0]["loc"]) or "update"
    return f"{location}: {errors[0]['type']}"
