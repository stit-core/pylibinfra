import asyncio
from typing import Any

import pytest
from aiogram.types import LabeledPrice, PreCheckoutQuery, User

from pylibinfra.tgbotkit import (
    ChargeGuard,
    Money,
    RefundGuard,
    answer_pre_checkout,
    build_invoice,
    build_subscription_link,
    refund_charge,
)
from pylibinfra.tgbotkit.payments.subscription import STARS_SUBSCRIPTION_PERIOD

from .conftest import FakeRedis


class FakeBot:
    def __init__(self, refund_ok: bool = True) -> None:
        self.answered: tuple[str, bool, str | None] | None = None
        self.invoice_link_kwargs: dict[str, Any] | None = None
        self.refunded: tuple[int, str] | None = None
        self._refund_ok = refund_ok

    async def answer_pre_checkout_query(
        self, pre_checkout_query_id: str, ok: bool, error_message: str | None = None
    ) -> bool:
        self.answered = (pre_checkout_query_id, ok, error_message)
        return True

    async def create_invoice_link(self, **kwargs: Any) -> str:
        self.invoice_link_kwargs = kwargs
        return "https://t.me/$invoice-link"

    async def refund_star_payment(self, user_id: int, telegram_payment_charge_id: str) -> bool:
        self.refunded = (user_id, telegram_payment_charge_id)
        return self._refund_ok


def _query() -> PreCheckoutQuery:
    return PreCheckoutQuery(
        id="q1",
        from_user=User(id=1, is_bot=False, first_name="T"),
        currency="XTR",
        total_amount=100,
        invoice_payload="order-1",
    )


async def test_pre_checkout_ok() -> None:
    bot = FakeBot()

    async def decide(_: PreCheckoutQuery) -> bool:
        return True

    ok = await answer_pre_checkout(bot, _query(), decide)  # type: ignore[arg-type]
    assert ok is True
    assert bot.answered == ("q1", True, None)


async def test_pre_checkout_rejected() -> None:
    bot = FakeBot()

    async def decide(_: PreCheckoutQuery) -> bool:
        return False

    ok = await answer_pre_checkout(bot, _query(), decide)  # type: ignore[arg-type]
    assert ok is False
    assert bot.answered is not None
    assert bot.answered[1] is False
    assert bot.answered[2] is not None  # error_message выставлен


async def test_pre_checkout_timeout_fails_closed() -> None:
    bot = FakeBot()

    async def slow(_: PreCheckoutQuery) -> bool:
        await asyncio.sleep(0.2)
        return True

    ok = await answer_pre_checkout(bot, _query(), slow, deadline=0.01)  # type: ignore[arg-type]
    assert ok is False
    assert bot.answered is not None
    assert bot.answered[1] is False


async def test_pre_checkout_exception_fails_closed() -> None:
    bot = FakeBot()

    async def boom(_: PreCheckoutQuery) -> bool:
        raise RuntimeError("backend down")

    ok = await answer_pre_checkout(bot, _query(), boom)  # type: ignore[arg-type]
    assert ok is False
    assert bot.answered is not None
    assert bot.answered[1] is False


async def test_charge_guard_reserve_dedup(fake_redis: FakeRedis) -> None:
    guard = ChargeGuard(fake_redis)  # type: ignore[arg-type]
    assert await guard.reserve("charge-1") is True  # захвачен
    assert await guard.reserve("charge-1") is False  # дубль пропущен
    assert await guard.reserve("charge-2") is True  # другой charge захвачен


async def test_charge_guard_release_allows_retry(fake_redis: FakeRedis) -> None:
    guard = ChargeGuard(fake_redis)  # type: ignore[arg-type]
    assert await guard.reserve("charge-1") is True
    await guard.release("charge-1")  # транзиентный сбой fulfill
    assert await guard.reserve("charge-1") is True  # повтор может выдать снова


async def test_charge_guard_commit_blocks_duplicate(fake_redis: FakeRedis) -> None:
    guard = ChargeGuard(fake_redis)  # type: ignore[arg-type]
    assert await guard.reserve("charge-1") is True
    await guard.commit("charge-1")  # выдача успешна → длинное окно
    assert await guard.reserve("charge-1") is False  # повтор платежа дропнут


def test_build_invoice_xtr_drops_provider_token() -> None:
    inv = build_invoice(
        title="Stars", description="d", payload="p", amount=50, provider_token="tok"
    )
    assert inv["currency"] == "XTR"
    assert inv["provider_token"] is None
    assert inv["prices"][0].amount == 50


def test_build_invoice_fiat_keeps_provider_token() -> None:
    inv = build_invoice(
        title="Pro", description="d", payload="p", amount=1000, currency="USD", provider_token="tok"
    )
    assert inv["currency"] == "USD"
    assert inv["provider_token"] == "tok"


def test_build_invoice_prices_list() -> None:
    lines = [LabeledPrice(label="Base", amount=100), LabeledPrice(label="Addon", amount=50)]
    inv = build_invoice(title="Combo", description="d", payload="p", prices=lines)
    assert [p.amount for p in inv["prices"]] == [100, 50]
    assert inv["currency"] == "XTR"


def test_build_invoice_requires_exactly_one_price_source() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        build_invoice(title="t", description="d", payload="p")  # ни amount, ни prices
    with pytest.raises(ValueError, match="exactly one"):
        build_invoice(
            title="t",
            description="d",
            payload="p",
            amount=1,
            prices=[LabeledPrice(label="x", amount=1)],
        )


def test_build_invoice_xtr_drops_tips() -> None:
    inv = build_invoice(
        title="Stars",
        description="d",
        payload="p",
        amount=50,
        max_tip_amount=10,
        suggested_tip_amounts=[5, 10],
    )
    assert "max_tip_amount" not in inv  # XTR не поддерживает чаевые
    assert "suggested_tip_amounts" not in inv


def test_build_invoice_fiat_keeps_tips() -> None:
    inv = build_invoice(
        title="Pro",
        description="d",
        payload="p",
        amount=1000,
        currency="USD",
        provider_token="tok",
        max_tip_amount=500,
        suggested_tip_amounts=[100, 250],
    )
    assert inv["max_tip_amount"] == 500
    assert inv["suggested_tip_amounts"] == [100, 250]


def test_money_stars_vs_fiat() -> None:
    assert Money(50).is_stars is True
    assert Money(50).currency == "XTR"
    assert Money(1000, "USD").is_stars is False


async def test_build_subscription_link_stars() -> None:
    bot = FakeBot()
    link = await build_subscription_link(
        bot,  # type: ignore[arg-type]
        title="Premium",
        description="monthly",
        payload="sub-1",
        price=Money(100),
    )
    assert link == "https://t.me/$invoice-link"
    kw = bot.invoice_link_kwargs
    assert kw is not None
    assert kw["currency"] == "XTR"
    assert kw["subscription_period"] == STARS_SUBSCRIPTION_PERIOD
    assert kw["prices"][0].amount == 100
    assert kw["provider_token"] is None


async def test_build_subscription_link_rejects_bad_xtr_period() -> None:
    bot = FakeBot()
    with pytest.raises(ValueError, match="subscription_period"):
        await build_subscription_link(
            bot,  # type: ignore[arg-type]
            title="P",
            description="d",
            payload="s",
            price=Money(100),
            subscription_period=60,
        )


async def test_refund_charge_ok() -> None:
    bot = FakeBot(refund_ok=True)
    ok = await refund_charge(bot, user_id=7, charge_id="ch-1")  # type: ignore[arg-type]
    assert ok is True
    assert bot.refunded == (7, "ch-1")


async def test_refund_charge_rejected() -> None:
    bot = FakeBot(refund_ok=False)
    ok = await refund_charge(bot, user_id=7, charge_id="ch-1")  # type: ignore[arg-type]
    assert ok is False


async def test_refund_guard_dedup(fake_redis: FakeRedis) -> None:
    guard = RefundGuard(fake_redis)  # type: ignore[arg-type]
    assert await guard.reserve("ch-1") is True  # откат выдачи захвачен
    assert await guard.reserve("ch-1") is False  # дубль refunded_payment пропущен


async def test_refund_guard_independent_of_charge_key(fake_redis: FakeRedis) -> None:
    # tg:refund:<id> не пересекается с tg:charge:<id> — выдача и возврат одного charge
    # дедупятся раздельно.
    charge = ChargeGuard(fake_redis)  # type: ignore[arg-type]
    refund = RefundGuard(fake_redis)  # type: ignore[arg-type]
    assert await charge.reserve("ch-1") is True
    assert await refund.reserve("ch-1") is True  # тот же charge_id, другой ключ


async def test_refund_guard_release_allows_retry(fake_redis: FakeRedis) -> None:
    guard = RefundGuard(fake_redis)  # type: ignore[arg-type]
    assert await guard.reserve("ch-1") is True
    await guard.release("ch-1")
    assert await guard.reserve("ch-1") is True
