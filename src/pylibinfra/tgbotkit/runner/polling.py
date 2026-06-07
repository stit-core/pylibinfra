"""PollingRunner — long-polling. start_polling сам делает emit_startup/shutdown и
graceful-stop по сигналам; allowed_updates вычисляем из реально зарегистрированных
хендлеров (не хардкодим). 1 инстанс (polling нельзя масштабировать).
"""

from __future__ import annotations

from .base import _BaseRunner


class PollingRunner(_BaseRunner):
    async def _serve(self) -> None:
        await self._dp.start_polling(
            self._bot,
            allowed_updates=self._dp.resolve_used_update_types(),
            drop_pending_updates=self._cfg.drop_pending_updates,
        )
