"""WebhookRunner — приём апдейтов вебхуком поверх httpkit (FastAPI + готовая
middleware-цепочка/health). POST-роут защищён shared_secret_guard'ом по
X-Telegram-Bot-Api-Secret-Token (его же ставим в set_webhook). HTTP-конфиг — из
HTTP_* env (httpkit.new_config). ≥2 инстанса безопасны (RedisEventIsolation).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from aiogram.types import Update
from fastapi import Depends, Request, Response

from pylibinfra import httpkit

from .base import _BaseRunner


class WebhookRunner(_BaseRunner):
    async def _handle(self, request: Request) -> Response:
        payload = await request.json()
        update = Update.model_validate(payload, context={"bot": self._bot})
        await self._dp.feed_update(self._bot, update)
        return Response(status_code=200)

    async def _serve(self) -> None:
        cfg = self._cfg
        http_cfg = httpkit.new_config()
        guard = httpkit.shared_secret_guard(
            httpkit.SharedSecretConfig(
                header="X-Telegram-Bot-Api-Secret-Token",
                secret=cfg.webhook_secret.get_secret_value(),
            )
        )
        server = httpkit.Server(http_cfg, self._log, probes=self._probes())
        server.app.add_api_route(
            cfg.webhook_path, self._handle, methods=["POST"], dependencies=[Depends(guard)]
        )

        await self._dp.emit_startup()
        await server.start()
        await self._bot.set_webhook(
            cfg.webhook_base_url.rstrip("/") + cfg.webhook_path,
            secret_token=cfg.webhook_secret.get_secret_value() or None,
            allowed_updates=self._dp.resolve_used_update_types(),
            drop_pending_updates=cfg.drop_pending_updates,
        )

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            # вне главного потока / не-POSIX add_signal_handler не поддерживается
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        try:
            await stop.wait()
        finally:
            await self._bot.delete_webhook()
            await server.stop()
            await self._dp.emit_shutdown()
