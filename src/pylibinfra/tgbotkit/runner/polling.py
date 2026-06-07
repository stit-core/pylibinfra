"""PollingRunner — long-polling. start_polling сам делает emit_startup/shutdown и
graceful-stop по сигналам; allowed_updates вычисляем из реально зарегистрированных
хендлеров (не хардкодим). 1 инстанс (polling нельзя масштабировать).

Параллельно поднимаем httpkit.Server только с health/ready-probes (redis/nats/bot),
чтобы k8s мог liveness/readiness-проверить polling-бот — апдейты он по-прежнему
тянет polling'ом, HTTP-порт несёт лишь probes.
"""

from __future__ import annotations

from pylibinfra import httpkit

from .base import _BaseRunner


class PollingRunner(_BaseRunner):
    async def _serve(self) -> None:
        server = httpkit.Server(httpkit.new_config(), self._log, probes=self._probes())
        await server.start()
        try:
            # start_polling блокирует и сам ловит SIGINT/SIGTERM → graceful-stop
            await self._dp.start_polling(
                self._bot,
                allowed_updates=self._dp.resolve_used_update_types(),
                drop_pending_updates=self._cfg.drop_pending_updates,
            )
        finally:
            await server.stop()
