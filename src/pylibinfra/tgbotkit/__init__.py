"""tgbotkit — bootstrap aiogram-бота как инфраструктурный примитив: Bot/Dispatcher/
RedisStorage, стандартная outer-цепочка (request-id/dedup/throttle/RED+span/
recovery), error-recovery, dishka-Update-scope, aiogram_dialog (setup последним),
polling|webhook runner с graceful-shutdown, и generic-механика подсистем (i18n/
notifications/payments/webapp). Знает про aiogram-как-transport (как httpkit про
FastAPI), НЕ про конкретный бот: тексты/команды/StatesGroup/флоу — в шаблоне через
точки расширения (RouterBundle/CommandBundle/NotifierBundle/ErrorUX/i18n).

Эмитит: tg_updates_total{type,result}, tg_update_duration_seconds,
tg_update_panics_total, tg_updates_deduped_total, tg_payments_total{stage,result};
span tg.update {type}.

Требует extra: `uv add "pylibinfra[tgbotkit]"` (aiogram, aiogram-dialog, fluent.runtime).
"""

try:
    import aiogram as _aiogram  # noqa: F401
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "tgbotkit requires the [tgbotkit] extra — install `pylibinfra[tgbotkit]` "
        "(aiogram, aiogram-dialog, fluent.runtime)."
    ) from _exc

from .config import Config, I18nConfig
from .errorux import ErrorUX, default_error_ux, register_error_handlers
from .extension import CommandBundle, CommandSpec, Notifier, NotifierBundle, RouterBundle
from .i18n import (
    ChainResolver,
    FluentTranslator,
    FromCachePref,
    FromUserLanguage,
    I18NFormat,
    LocalePrefStore,
    LocaleResolver,
    Translator,
    install_i18n,
)
from .metrics import TgInstruments, new_instruments
from .module import (
    BotProvider,
    OptionalLocaleResolver,
    OptionalStreamBus,
    OptionalTranslator,
    new_config,
    probe,
)
from .notifications import (
    Delivery,
    DeliveryDedup,
    NotificationRouter,
    NotificationRunner,
)
from .payments import answer_pre_checkout, build_invoice, guard_charge
from .runner import BotRunner, PollingRunner, WebhookRunner, build_runner
from .webapp import InitDataError, validate_init_data

__all__ = [
    "BotProvider",
    "BotRunner",
    "ChainResolver",
    "CommandBundle",
    "CommandSpec",
    "Config",
    "Delivery",
    "DeliveryDedup",
    "ErrorUX",
    "FluentTranslator",
    "FromCachePref",
    "FromUserLanguage",
    "I18NFormat",
    "I18nConfig",
    "InitDataError",
    "LocalePrefStore",
    "LocaleResolver",
    "NotificationRouter",
    "NotificationRunner",
    "Notifier",
    "NotifierBundle",
    "OptionalLocaleResolver",
    "OptionalStreamBus",
    "OptionalTranslator",
    "PollingRunner",
    "RouterBundle",
    "TgInstruments",
    "Translator",
    "WebhookRunner",
    "answer_pre_checkout",
    "build_invoice",
    "build_runner",
    "default_error_ux",
    "guard_charge",
    "install_i18n",
    "new_config",
    "new_instruments",
    "probe",
    "register_error_handlers",
    "validate_init_data",
]
