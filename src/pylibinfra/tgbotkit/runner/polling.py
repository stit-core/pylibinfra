"""PollingRunner — long-polling плюс HTTP-порт под пробы."""

from __future__ import annotations

import asyncio
import contextlib

from .base import _BaseRunner


class PollingRunner(_BaseRunner):
    """Тянет апдейты long-polling'ом; HTTP-сервер несёт только `/health` и `/ready`.

    Масштабировать polling нельзя: второй инстанс получал бы `409 terminated by other
    getUpdates`. `allowed_updates` вычисляется из фактически зарегистрированных
    обработчиков, а не хардкодится.
    """

    async def _serve(self) -> None:
        http = await self._serve_probes()
        # start_polling сам ловит SIGINT/SIGTERM; сессию бота закрывает контейнер.
        polling = asyncio.create_task(
            self._dp.start_polling(
                self._bot,
                allowed_updates=self._dp.resolve_used_update_types(),
                drop_pending_updates=self._cfg.drop_pending_updates,
                close_bot_session=False,
            )
        )
        requested = asyncio.create_task(self._stopped.wait())
        try:
            await asyncio.wait({polling, requested}, return_when=asyncio.FIRST_COMPLETED)
            await self._stop_polling(polling)
        finally:
            requested.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await requested
            # Через `_step`: сорвавшаяся отмена опроса не имеет права оставить
            # занятым порт, а сбой остановки сервера — подменить причину выхода.
            await self._step("polling", self._drop_polling(polling))
            await self._step("http", self._stop_http(http))

    async def _stop_polling(self, polling: asyncio.Task[None]) -> None:
        """Просит диспетчер остановиться и ждёт его в пределах `BOT_SHUTDOWN_TIMEOUT`."""
        if polling.done():
            await polling
            return
        try:
            async with asyncio.timeout(self._cfg.shutdown_timeout):
                # Стоп мог прийти раньше, чем задача дошла до getUpdates: aiogram
                # отвечает на такой stop_polling RuntimeError, а не тихим no-op.
                with contextlib.suppress(RuntimeError):
                    await self._dp.stop_polling()
                await polling
        except TimeoutError:
            self._log.warn(
                "tgbotkit: polling stop timed out, cancelling",
                timeout=self._cfg.shutdown_timeout,
            )

    async def _drop_polling(self, polling: asyncio.Task[None]) -> None:
        if polling.done():
            return
        polling.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await polling
