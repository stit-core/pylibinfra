"""BotProvider — единственная точка входа kit'а (dishka-first). Прячет
Bot/Dispatcher/RedisStorage/middleware/error-handlers/runner. Config НЕ
provide'ит (asymmetry note) — caller регистрирует сам (new_config ниже).
redis.Redis/Cache/Logger берутся из cachekit/logkit (kit-on-kit). Точки
расширения (Router/Command/Notifier-Bundle, ErrorUX, Translator/Resolver, Bus) —
пустые/None дефолты тут; шаблон override'ит своим Provider'ом ПОСЛЕ BotProvider.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage
from dishka import AsyncContainer, Provider, Scope, provide

from pylibinfra.logkit import Logger, nop
from pylibinfra.natskit import StreamBus

from .config import Config
from .errorux import ErrorUX, default_error_ux, register_error_handlers
from .extension import CommandBundle, NotifierBundle, RouterBundle
from .i18n import LocaleResolver, Translator, install_i18n
from .metrics import TgInstruments, new_instruments
from .middleware import install_standard_middleware
from .runner import BotRunner, build_runner
from .storage import build_storage

# optional-зависимости через type-wrappers (dishka не умеет optional по X | None):
# kit даёт None-дефолт, consumer override'ит своим Provider'ом (last-wins).
OptionalStreamBus = StreamBus | None
OptionalTranslator = Translator | None
OptionalLocaleResolver = LocaleResolver | None


class BotProvider(Provider):
    scope = Scope.APP

    @provide
    def default_logger(self) -> Logger:
        return nop()

    @provide
    def default_routers(self) -> RouterBundle:
        return ()

    @provide
    def default_commands(self) -> CommandBundle:
        return ()

    @provide
    def default_notifiers(self) -> NotifierBundle:
        return ()

    @provide
    def default_error_ux(self) -> ErrorUX:
        return default_error_ux()

    @provide
    def default_bus(self) -> OptionalStreamBus:
        return None

    @provide
    def default_translator(self) -> OptionalTranslator:
        return None

    @provide
    def default_resolver(self) -> OptionalLocaleResolver:
        return None

    @provide
    def instruments(self) -> TgInstruments:
        return new_instruments()

    @provide
    async def bot(self, cfg: Config) -> AsyncIterator[Bot]:
        b = Bot(cfg.token, default=DefaultBotProperties(parse_mode=cfg.parse_mode))
        yield b
        await b.session.close()  # после container.close (lifecycle 06 §5)

    @provide
    def storage(self, client: redis.Redis) -> RedisStorage:
        return build_storage(client)

    @provide
    def dispatcher(
        self,
        storage: RedisStorage,
        client: redis.Redis,
        log: Logger,
        cfg: Config,
        instruments: TgInstruments,
        routers: RouterBundle,
        commands: CommandBundle,
        errux: ErrorUX,
        translator: OptionalTranslator,
        resolver: OptionalLocaleResolver,
    ) -> Dispatcher:
        dp = Dispatcher(storage=storage)
        install_standard_middleware(dp, cfg, log, instruments, client, errux)
        install_i18n(dp, cfg.i18n, translator, resolver)  # no-op без каталога/translator
        for spec in commands:
            dp.observers[spec.observer].register(spec.handler, *spec.filters)
        register_error_handlers(dp, errux)
        for router in routers:
            dp.include_router(router)
        # setup_dishka/setup_dialogs — deferred в BotRunner.run (нужен container)
        return dp

    @provide
    def runner(
        self,
        cfg: Config,
        bot: Bot,
        dp: Dispatcher,
        client: redis.Redis,
        log: Logger,
        notifiers: NotifierBundle,
        bus: OptionalStreamBus,
        container: AsyncContainer,
    ) -> BotRunner:
        return build_runner(
            cfg=cfg,
            bot=bot,
            dp=dp,
            container=container,
            client=client,
            log=log,
            notifiers=notifiers,
            bus=bus,
        )


def probe(bot: Bot) -> Callable[[], Awaitable[None]]:
    """Health-probe: get_me(). Не provide'ится (тип Callable[[], Awaitable[None]]
    коллизит с любым другим probe) — caller регистрирует с нужным тегом сам."""

    async def check() -> None:
        await bot.get_me()

    return check


def new_config() -> Config:
    return Config()
