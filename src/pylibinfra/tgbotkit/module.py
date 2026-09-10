"""dishka-проводка кита: `Bot`, `Dispatcher`, `BotRunner` и фабрики health-проб."""

from __future__ import annotations

from collections.abc import AsyncIterator

import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.redis import RedisStorage
from dishka import AsyncContainer, Provider, Scope, provide

from pylibinfra import healthkit, httpkit
from pylibinfra.clockkit import Clock
from pylibinfra.logkit import Logger
from pylibinfra.natskit import StreamBus

from .config import Config
from .errors import TgbotkitError
from .errorux import ErrorUX, default_error_ux, register_error_handlers
from .extension import CommandBundle, NotifierBundle, RouterBundle
from .i18n import LocaleResolver, Translator, install_i18n
from .metrics import Instruments, new_instruments
from .middleware import OutgoingThrottleMiddleware, install_standard_middleware
from .runner import BotRunner, build_runner
from .storage import build_storage

# dishka не инжектит по `X | None`, поэтому опциональные зависимости выражены типами-
# обёртками: кит даёт None-дефолт, консьюмер перекрывает его своим Provider'ом.
OptionalStreamBus = StreamBus | None
OptionalTranslator = Translator | None
OptionalLocaleResolver = LocaleResolver | None

PROBE_NAME = "tgbotkit"
REDIS_PROBE_NAME = "tgbotkit/redis"


class BotProvider(Provider):
    """Граф кита: `Bot` → `Dispatcher` → `BotRunner`.

    Консьюмер обязан положить в контейнер `Config`, `logkit.Logger`, `clockkit.Clock`,
    `redis.Redis` (`cachekit.CachekitProvider`) и `httpkit.Server`, собранный как
    `HttpProvider(autostart=False)`: его lifecycle ведёт runner. Пробы кита в
    `healthkit.Probes` складывает тоже консьюмер — `probe`/`redis_probe` ниже.
    """

    scope = Scope.APP

    @provide
    def default_routers(self) -> RouterBundle:
        """Пустой набор роутеров и диалогов; консьюмер перекрывает своим Provider'ом."""
        return ()

    @provide
    def default_commands(self) -> CommandBundle:
        """Пустой набор входов диспетчера."""
        return ()

    @provide
    def default_notifiers(self) -> NotifierBundle:
        """Пустой набор фоновых подписчиков: бот работает без NATS."""
        return ()

    @provide
    def default_error_ux(self) -> ErrorUX:
        """Прод-безопасный error-UX кита."""
        return default_error_ux()

    @provide
    def default_bus(self) -> OptionalStreamBus:
        """Без `StreamBus` фоновые подписки не поднимаются."""
        return None

    @provide
    def default_translator(self) -> OptionalTranslator:
        """Без движка переводов i18n собирается из `BOT_I18N_PATH` либо выключен."""
        return None

    @provide
    def default_resolver(self) -> OptionalLocaleResolver:
        """Дефолтный выбор локали — язык клиента Telegram."""
        return None

    @provide
    def instruments(self) -> Instruments:
        """Инструменты кита на текущем `MeterProvider`."""
        return new_instruments()

    @provide
    async def bot(
        self,
        cfg: Config,
        client: redis.Redis,
        log: Logger,
        clock: Clock,
        instruments: Instruments,
    ) -> AsyncIterator[Bot]:
        """`Bot` с таймаутом Bot API; сессия закрывается на закрытии контейнера."""
        instance = Bot(
            cfg.token.get_secret_value(),
            session=AiohttpSession(timeout=cfg.api_timeout),
            default=DefaultBotProperties(parse_mode=cfg.parse_mode or None),
        )
        if cfg.flood_enabled:
            instance.session.middleware(
                OutgoingThrottleMiddleware(client, cfg, log, clock, instruments)
            )
        try:
            yield instance
        finally:
            await instance.session.close()

    @provide
    def storage(self, client: redis.Redis, cfg: Config) -> RedisStorage:
        """FSM-хранилище; один `BOT_STATE_TTL` гонит и state, и данные диалога."""
        return build_storage(client, cfg.state_ttl, cfg.state_ttl)

    @provide
    def dispatcher(
        self,
        storage: RedisStorage,
        client: redis.Redis,
        log: Logger,
        cfg: Config,
        instruments: Instruments,
        routers: RouterBundle,
        commands: CommandBundle,
        errux: ErrorUX,
        translator: OptionalTranslator,
        resolver: OptionalLocaleResolver,
    ) -> Dispatcher:
        """Диспетчер с цепочкой кита, i18n, входами и обработчиками ошибок консьюмера.

        `setup_dishka`/`setup_dialogs` здесь не зовутся: им нужен готовый контейнер,
        поэтому их делает `BotRunner.run`.
        """
        dp = Dispatcher(storage=storage)
        install_standard_middleware(dp, cfg, log, instruments, client, errux)
        install_i18n(dp, cfg.i18n, translator, resolver)
        for spec in commands:
            dp.observers[spec.observer].register(spec.handler, *spec.filters)
        register_error_handlers(dp, errux)
        for router in routers:
            dp.include_router(router)
        return dp

    @provide
    def runner(
        self,
        cfg: Config,
        bot: Bot,
        dp: Dispatcher,
        client: redis.Redis,
        server: httpkit.Server,
        log: Logger,
        notifiers: NotifierBundle,
        bus: OptionalStreamBus,
        container: AsyncContainer,
    ) -> BotRunner:
        """Runner под `BOT_MODE`; апдейты пойдут только после `await runner.run()`."""
        return build_runner(
            cfg=cfg,
            bot=bot,
            dp=dp,
            container=container,
            client=client,
            server=server,
            log=log,
            notifiers=notifiers,
            bus=bus,
        )


def probe(bot: Bot) -> healthkit.Probe:
    """Проба `tgbotkit`: `getMe`. Ходит в Telegram, поэтому её стоит ограничить дедлайном."""

    async def check() -> None:
        try:
            await bot.get_me()
        except Exception as exc:
            raise TgbotkitError(f"tgbotkit: get_me: {exc}") from exc

    return healthkit.new(PROBE_NAME, check)


def redis_probe(client: redis.Redis) -> healthkit.Probe:
    """Проба `tgbotkit/redis`: без Redis нет ни FSM-состояния, ни дедупа апдейтов."""

    async def check() -> None:
        try:
            await client.ping()
        except Exception as exc:
            raise TgbotkitError(f"tgbotkit: redis ping: {exc}") from exc

    return healthkit.new(REDIS_PROBE_NAME, check)
