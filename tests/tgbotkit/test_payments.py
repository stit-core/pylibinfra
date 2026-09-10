"""payments: pre-checkout в дедлайн, дедуп платежей, инвойсы, подписки и возвраты."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.types import LabeledPrice, PreCheckoutQuery, User

from pylibinfra.clockkit import Tickable
from pylibinfra.testkit.bottest import MemoryRedis
from pylibinfra.testkit.logtest import Capture
from pylibinfra.tgbotkit import (
    STARS_SUBSCRIPTION_PERIOD,
    ChargeGuard,
    Money,
    PaymentError,
    RefundGuard,
    answer_pre_checkout,
    build_invoice,
    build_subscription_link,
    new_instruments,
    refund_charge,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


class FakeBot:
    def __init__(
        self,
        refund_ok: bool = True,
        refund_raises: bool = False,
        answer_raises: bool = False,
    ) -> None:
        self.answered: tuple[str, bool, str | None] | None = None
        self.invoice_link_kwargs: dict[str, Any] | None = None
        self.refunded: tuple[int, str] | None = None
        self._refund_ok = refund_ok
        self._refund_raises = refund_raises
        self._answer_raises = answer_raises

    async def answer_pre_checkout_query(
        self, pre_checkout_query_id: str, ok: bool, error_message: str | None = None
    ) -> bool:
        if self._answer_raises:
            raise RuntimeError("telegram is down")
        self.answered = (pre_checkout_query_id, ok, error_message)
        return True

    async def create_invoice_link(self, **kwargs: Any) -> str:
        self.invoice_link_kwargs = kwargs
        return "https://t.me/$invoice-link"

    async def refund_star_payment(self, user_id: int, telegram_payment_charge_id: str) -> bool:
        if self._refund_raises:
            raise RuntimeError("telegram is down")
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


async def test_pre_checkout_confirms(read_metric: Callable[..., float]) -> None:
    bot = FakeBot()

    async def decide(_: PreCheckoutQuery) -> bool:
        return True

    assert await answer_pre_checkout(bot, _query(), decide) is True  # type: ignore[arg-type]
    assert bot.answered == ("q1", True, None)
    assert read_metric("tg_payments_total", {"stage": "pre_checkout", "result": "ok"}) == 1


async def test_pre_checkout_rejects(read_metric: Callable[..., float]) -> None:
    bot = FakeBot()

    async def decide(_: PreCheckoutQuery) -> bool:
        return False

    assert await answer_pre_checkout(bot, _query(), decide) is False  # type: ignore[arg-type]
    assert bot.answered is not None
    assert bot.answered[2] is not None
    assert read_metric("tg_payments_total", {"stage": "pre_checkout", "result": "rejected"}) == 1


async def test_pre_checkout_timeout_fails_closed(read_metric: Callable[..., float]) -> None:
    bot = FakeBot()

    async def slow(_: PreCheckoutQuery) -> bool:
        await asyncio.sleep(1)
        return True

    assert await answer_pre_checkout(bot, _query(), slow, deadline=0.01) is False  # type: ignore[arg-type]
    assert read_metric("tg_payments_total", {"stage": "pre_checkout", "result": "error"}) == 1


async def test_pre_checkout_exception_fails_closed() -> None:
    bot = FakeBot()
    log = Capture()

    async def boom(_: PreCheckoutQuery) -> bool:
        raise RuntimeError("backend down")

    assert await answer_pre_checkout(bot, _query(), boom, log=log) is False  # type: ignore[arg-type]
    assert bot.answered is not None
    assert bot.answered[1] is False
    # Без записи в лог от причины отказа остался бы только счётчик.
    entry = log.find("tgbotkit: pre-checkout validation failed, refusing", level="error")
    assert entry is not None
    assert "backend down" in entry.str("err")


async def test_pre_checkout_counts_a_failed_answer(read_metric: Callable[..., float]) -> None:
    async def decide(_: PreCheckoutQuery) -> bool:
        return True

    with pytest.raises(RuntimeError):
        await answer_pre_checkout(FakeBot(answer_raises=True), _query(), decide)  # type: ignore[arg-type]
    assert read_metric("tg_payments_total", {"stage": "pre_checkout", "result": "error"}) == 1


async def test_charge_guard_reserves_once(redis_client: MemoryRedis) -> None:
    guard = ChargeGuard(redis_client)  # type: ignore[arg-type]
    assert await guard.reserve("charge-1") is not None
    assert await guard.reserve("charge-1") is None
    assert await guard.reserve("charge-2") is not None


async def test_charge_guard_duplicate_is_counted(read_metric: Callable[..., float]) -> None:
    guard = ChargeGuard(MemoryRedis(), instruments=new_instruments())  # type: ignore[arg-type]
    await guard.reserve("charge-1")
    await guard.reserve("charge-1")
    assert read_metric("tg_payments_total", {"stage": "paid", "result": "duplicate"}) == 1


async def test_charge_guard_release_allows_retry(redis_client: MemoryRedis) -> None:
    guard = ChargeGuard(redis_client)  # type: ignore[arg-type]
    token = await guard.reserve("charge-1")
    assert token is not None
    assert await guard.release("charge-1", token) is True
    assert await guard.reserve("charge-1") is not None


async def test_charge_guard_commit_blocks_duplicate(redis_client: MemoryRedis) -> None:
    guard = ChargeGuard(redis_client)  # type: ignore[arg-type]
    token = await guard.reserve("charge-1")
    assert token is not None
    assert await guard.commit("charge-1", token) is True
    assert await guard.reserve("charge-1") is None


async def test_charge_guard_release_after_commit_is_refused(redis_client: MemoryRedis) -> None:
    guard = ChargeGuard(redis_client)  # type: ignore[arg-type]
    token = await guard.reserve("charge-1")
    assert token is not None
    await guard.commit("charge-1", token)

    # Свой же токен зафиксированный платёж не отпирает: иначе повторный апдейт
    # прошёл бы через reserve и выдача случилась бы дважды.
    assert await guard.release("charge-1", token) is False
    assert await guard.reserve("charge-1") is None


async def test_expired_reservation_is_not_released_by_its_owner() -> None:
    clock = Tickable(START)
    client = MemoryRedis(clock=clock)
    first = ChargeGuard(client, reserve_ttl=60.0)  # type: ignore[arg-type]
    stale = await first.reserve("charge-1")
    assert stale is not None

    clock.advance(61.0)
    second = await ChargeGuard(client).reserve("charge-1")  # type: ignore[arg-type]
    assert second is not None

    # Опоздавший воркер снимает не свой резерв — второй бы обработал платёж заново.
    assert await first.release("charge-1", stale) is False
    assert await first.commit("charge-1", stale) is False
    assert await ChargeGuard(client).reserve("charge-1") is None  # type: ignore[arg-type]


async def test_refund_guard_is_independent_of_charge_key(redis_client: MemoryRedis) -> None:
    assert await ChargeGuard(redis_client).reserve("ch-1") is not None  # type: ignore[arg-type]
    assert await RefundGuard(redis_client).reserve("ch-1") is not None  # type: ignore[arg-type]


async def test_refund_guard_duplicate_is_counted(read_metric: Callable[..., float]) -> None:
    guard = RefundGuard(MemoryRedis(), instruments=new_instruments())  # type: ignore[arg-type]
    await guard.reserve("ch-1")
    assert await guard.reserve("ch-1") is None
    assert read_metric("tg_payments_total", {"stage": "refunded", "result": "duplicate"}) == 1


async def test_refund_guard_release_allows_retry(redis_client: MemoryRedis) -> None:
    guard = RefundGuard(redis_client)  # type: ignore[arg-type]
    token = await guard.reserve("ch-1")
    assert token is not None
    await guard.release("ch-1", token)
    assert await guard.reserve("ch-1") is not None


async def test_reservation_expires_with_its_window() -> None:
    clock = Tickable(START)
    guard = ChargeGuard(MemoryRedis(clock=clock), reserve_ttl=30.0)  # type: ignore[arg-type]
    assert await guard.reserve("ch-1") is not None

    clock.advance(29.0)
    assert await guard.reserve("ch-1") is None
    clock.advance(2.0)
    assert await guard.reserve("ch-1") is not None


def test_invoice_stars_drop_provider_token_and_tips() -> None:
    invoice = build_invoice(
        title="Stars",
        description="d",
        payload="p",
        amount=50,
        provider_token="tok",
        max_tip_amount=10,
        suggested_tip_amounts=[5, 10],
    )
    assert invoice["currency"] == "XTR"
    assert invoice["provider_token"] is None
    assert invoice["prices"][0].amount == 50
    assert "max_tip_amount" not in invoice


def test_invoice_fiat_keeps_provider_token_and_tips() -> None:
    invoice = build_invoice(
        title="Pro",
        description="d",
        payload="p",
        amount=1000,
        currency="USD",
        provider_token="tok",
        max_tip_amount=500,
        suggested_tip_amounts=[100, 250],
    )
    assert invoice["provider_token"] == "tok"
    assert invoice["max_tip_amount"] == 500
    assert invoice["suggested_tip_amounts"] == [100, 250]


def test_invoice_fiat_tips_without_suggestions() -> None:
    invoice = build_invoice(
        title="Pro",
        description="d",
        payload="p",
        amount=1000,
        currency="USD",
        provider_token="tok",
        max_tip_amount=500,
    )
    assert "suggested_tip_amounts" not in invoice


def test_invoice_fiat_without_provider_token_rejected() -> None:
    # Telegram отбил бы такой инвойс 400: провайдер обязателен для всего, кроме звёзд.
    with pytest.raises(PaymentError, match="provider_token is required"):
        build_invoice(title="Pro", description="d", payload="p", amount=1000, currency="USD")


def test_invoice_accepts_composite_prices() -> None:
    lines = [LabeledPrice(label="Base", amount=100), LabeledPrice(label="Addon", amount=50)]
    invoice = build_invoice(title="Combo", description="d", payload="p", prices=lines)
    assert [p.amount for p in invoice["prices"]] == [100, 50]


def test_invoice_passes_extra_kwargs_through() -> None:
    invoice = build_invoice(
        title="t", description="d", payload="p", amount=1, photo_url="https://x/y.png"
    )
    assert invoice["photo_url"] == "https://x/y.png"


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"amount": 1, "prices": [LabeledPrice(label="x", amount=1)]}],
)
def test_invoice_requires_exactly_one_price_source(kwargs: dict[str, Any]) -> None:
    with pytest.raises(PaymentError, match="exactly one"):
        build_invoice(title="t", description="d", payload="p", **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"amount": 0}, "amount must be > 0"), ({"prices": []}, "must not be empty")],
)
def test_invoice_rejects_empty_charge(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(PaymentError, match=message):
        build_invoice(title="t", description="d", payload="p", **kwargs)


def test_money_distinguishes_stars_from_fiat() -> None:
    assert Money(50).is_stars is True
    assert Money(1000, "USD").is_stars is False


async def test_subscription_link_for_stars() -> None:
    bot = FakeBot()
    link = await build_subscription_link(
        bot,  # type: ignore[arg-type]
        title="Premium",
        description="monthly",
        payload="sub-1",
        price=Money(100),
    )
    assert link == "https://t.me/$invoice-link"
    kwargs = bot.invoice_link_kwargs
    assert kwargs is not None
    assert kwargs["currency"] == "XTR"
    assert kwargs["subscription_period"] == STARS_SUBSCRIPTION_PERIOD
    assert kwargs["provider_token"] is None


async def test_subscription_link_rejects_non_monthly_stars() -> None:
    with pytest.raises(PaymentError, match="subscription_period"):
        await build_subscription_link(
            FakeBot(),  # type: ignore[arg-type]
            title="P",
            description="d",
            payload="s",
            price=Money(100),
            subscription_period=60,
        )


async def test_subscription_link_rejects_free_price() -> None:
    with pytest.raises(PaymentError, match="price must be > 0"):
        await build_subscription_link(
            FakeBot(),  # type: ignore[arg-type]
            title="P",
            description="d",
            payload="s",
            price=Money(0),
        )


async def test_subscription_link_rejects_fiat_without_provider_token() -> None:
    with pytest.raises(PaymentError, match="provider_token is required"):
        await build_subscription_link(
            FakeBot(),  # type: ignore[arg-type]
            title="P",
            description="d",
            payload="s",
            price=Money(1000, "USD"),
            subscription_period=60,
        )


async def test_subscription_link_for_fiat_keeps_provider_token() -> None:
    bot = FakeBot()
    await build_subscription_link(
        bot,  # type: ignore[arg-type]
        title="P",
        description="d",
        payload="s",
        price=Money(1000, "USD"),
        subscription_period=60,
        provider_token="tok",
    )
    assert bot.invoice_link_kwargs is not None
    assert bot.invoice_link_kwargs["provider_token"] == "tok"


async def test_refund_ok(read_metric: Callable[..., float]) -> None:
    bot = FakeBot(refund_ok=True)
    assert await refund_charge(bot, user_id=7, charge_id="ch-1") is True  # type: ignore[arg-type]
    assert bot.refunded == (7, "ch-1")
    assert read_metric("tg_payments_total", {"stage": "refund", "result": "ok"}) == 1


async def test_refund_rejected(read_metric: Callable[..., float]) -> None:
    assert await refund_charge(FakeBot(refund_ok=False), user_id=7, charge_id="ch-1") is False  # type: ignore[arg-type]
    assert read_metric("tg_payments_total", {"stage": "refund", "result": "rejected"}) == 1


async def test_refund_error_is_counted_and_reraised(read_metric: Callable[..., float]) -> None:
    with pytest.raises(RuntimeError):
        await refund_charge(FakeBot(refund_raises=True), user_id=7, charge_id="ch-1")  # type: ignore[arg-type]
    assert read_metric("tg_payments_total", {"stage": "refund", "result": "error"}) == 1
