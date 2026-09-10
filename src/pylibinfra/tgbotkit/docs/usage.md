# Рецепты tgbotkit

## Диалог и команда

Диалоги и роутеры едут в `RouterBundle`, не-диалоговые входы диспетчера — в `CommandBundle`:

```python
from aiogram.filters import Command
from aiogram_dialog import Dialog, DialogManager, StartMode, Window
from aiogram_dialog.widgets.text import Const
from dishka import Provider, Scope, provide

from pylibinfra import tgbotkit

main_dialog = Dialog(Window(Const("Меню"), state=Menu.main))


async def start(message, dialog_manager: DialogManager) -> None:
    await dialog_manager.start(Menu.main, mode=StartMode.RESET_STACK)


class BotProviders(Provider):
    scope = Scope.APP

    @provide
    def routers(self) -> tgbotkit.RouterBundle:
        return (main_dialog,)

    @provide
    def commands(self) -> tgbotkit.CommandBundle:
        return (tgbotkit.CommandSpec("message", start, (Command("start"),)),)
```

`observer` в `CommandSpec` — ключ в `dp.observers`: `message`, `callback_query`,
`pre_checkout_query`, `message` с фильтром `F.successful_payment` и так далее.

## Тексты через i18n

Виджет хранит **ключ**, а не текст; локаль подставляет middleware:

```python
from pylibinfra.tgbotkit import I18NFormat

Window(I18NFormat("menu-title"), state=Menu.main)
```

`BOT_I18N_PATH=/app/locales` с каталогами `/app/locales/{en,ru}/main.ftl` включает подсистему.
Явный выбор языка пользователем хранится в `cachekit`:

```python
from pylibinfra.tgbotkit import ChainResolver, FromCachePref, FromUserLanguage, LocalePrefStore


@provide
def locale_resolver(self, cache: cachekit.Cache) -> tgbotkit.OptionalLocaleResolver:
    return ChainResolver(FromCachePref(LocalePrefStore(cache)), FromUserLanguage())
```

Обработчик получает локаль в `data["locale"]`, фоновая доставка — через `Delivery.localize`.

## Уведомления из JetStream

```python
from pylibinfra import natskit, tgbotkit


async def on_order_paid(payload: dict, delivery: tgbotkit.Delivery) -> None:
    chat_id = await accounts.chat_for(payload["user_id"])  # маппинг — продуктовый
    await delivery.send_message(chat_id=chat_id, text=delivery.localize(None, "order-paid"))


class NotifyProvider(Provider):
    scope = Scope.APP

    @provide
    def notifiers(self) -> tgbotkit.NotifierBundle:
        router = tgbotkit.NotificationRouter()
        router.on("ORDERS", "orders.paid", "bot-orders-paid", on_order_paid)
        return (router,)

    @provide
    def bus(self, stream_bus: natskit.StreamBus) -> tgbotkit.OptionalStreamBus:
        return stream_bus
```

Подписки поднимаются на старте бота и дренятся на остановке в пределах `BOT_SHUTDOWN_TIMEOUT`.
Исключение из обработчика — это `NAK` и переотправка, поэтому обработчик обязан быть
идемпотентным; для этого есть `DeliveryDedup`:

```python
dedup = tgbotkit.DeliveryDedup(redis_client)


async def on_order_paid(payload: dict, delivery: tgbotkit.Delivery) -> None:
    key = payload["event_id"]
    token = await dedup.reserve(key)
    if token is None:
        return  # уже доставлено или доставляется прямо сейчас
    try:
        await delivery.send_message(chat_id=..., text=...)
    except Exception:
        await dedup.release(key, token)  # переотправка повторит сразу
        raise
    await dedup.commit(key, token)
```

`reserve` отдаёт токен владения; `commit`/`release` без него чужой резерв не трогают —
истёкший на медленной доставке ключ мог уже перехватить соседний воркер, и `False` из
`commit` означает ровно это (стоит записать в лог, а не проглотить).

Чтобы открыть пользователю диалог из фона, есть `delivery.start_dialog(user_id=…, chat_id=…,
state=…)`: он поднимает реальные `Chat`/`User` (`load=True`), иначе AUTO-режим показа сообщений
работает неверно.

## Динамика в MarkdownV2

Служебный символ вне разметки Telegram трактует как ошибку разбора, а не как текст, поэтому любая
подстановка экранируется до склейки:

```python
from pylibinfra.tgbotkit import escape_markdown_v2, escape_markdown_v2_code

text = f"*{escape_markdown_v2(title)}*\n```{escape_markdown_v2_code(sql)}```"
```

Если разметка всё же отбита, фоновая доставка переотправит сообщение без неё — содержимое важнее
оформления.

## Платежи

```python
from aiogram import F
from aiogram.types import Message, PreCheckoutQuery

from pylibinfra import logkit, tgbotkit


async def pre_checkout(query: PreCheckoutQuery, bot, log: logkit.Logger) -> None:
    await tgbotkit.answer_pre_checkout(bot, query, orders.validate, log=log)


async def paid(message: Message, bot) -> None:
    charge_id = message.successful_payment.telegram_payment_charge_id
    token = await guard.reserve(charge_id)
    if token is None:
        return  # платёж уже обработан или обрабатывается прямо сейчас
    try:
        await orders.fulfill(charge_id)
    except Exception:
        await guard.release(charge_id, token)
        raise
    await guard.commit(charge_id, token)


commands = (
    tgbotkit.CommandSpec("pre_checkout_query", pre_checkout),
    tgbotkit.CommandSpec("message", paid, (F.successful_payment,)),
)
```

`decide` внутри `answer_pre_checkout` обязан сверять цену на бэкенде: `total_amount` и
`invoice_payload` приходят от клиента. Без `log=` причина отказа гаснет вместе с исключением —
остаётся только `tg_payments_total{result=error}`. `ChargeGuard` и `RefundGuard` держат
раздельные ключи, поэтому возврат не блокирует выдачу того же `charge_id`.

Инвойс собирает `build_invoice` (`amount=` — одна строка, `prices=` — составной чек),
подписку — `build_subscription_link`. На `XTR` `provider_token` не нужен и чаевые
отбрасываются; для фиата `provider_token` обязателен и его отсутствие — `PaymentError`
до вызова Telegram.

## WebApp

```python
from pylibinfra import httpkit, tgbotkit


@app.post("/webapp/session")
async def session(init_data: str) -> dict[str, str]:
    try:
        fields = tgbotkit.validate_init_data(init_data, token)
    except tgbotkit.InitDataError as exc:
        raise httpkit.unauthorized("BAD_INIT_DATA", "invalid init data") from exc
    return await accounts.session_for(fields["user"])
```

## Свой ErrorUX

```python
class BrandedErrorUX:
    async def on_unknown_intent(self, event, bot) -> None:
        await event.update.callback_query.answer("Сессия истекла, нажмите /start")

    async def on_unknown_state(self, event, bot) -> None:
        await self.on_unknown_intent(event, bot)

    async def on_unhandled(self, event, bot) -> None:
        await event.update.message.answer("Не получилось, мы уже разбираемся")


@provide
def error_ux(self) -> tgbotkit.ErrorUX:
    return BrandedErrorUX()
```

Текст обязан оставаться нейтральным: внутренние сообщения об ошибках пользователю не показываются
никогда — они уходят в лог с `request_id`.

## Вебхук рядом со своим API

Роут вебхука регистрируется на том же `httpkit.Server`, что и API сервиса, поэтому WebApp-бэкенд
и бот живут на одном порту:

```python
container = make_async_container(
    tgbotkit.BotProvider(),
    httpkit.HttpProvider(autostart=False),
    ...,
)
app = await container.get(FastAPI)
app.include_router(webapp_router)  # роуты сервиса — до run()
await (await container.get(tgbotkit.BotRunner)).run()
```

Гейт вебхука проверяет `X-Telegram-Bot-Api-Secret-Token`; если в контейнере есть
`httpkit.AuthExtractor`, путь вебхука нужно объявить публичным (`HTTP_QUIET_PATHS` или
собственный extractor), иначе deny-by-default отобьёт апдейты Telegram.

## Остановка

`runner.stop()` просит завершиться из кода — тот же путь, что и SIGTERM: подписки дренятся,
вебхук снимается, сервер гасится, контейнер закрывается. Ждать нужно `run()`.
