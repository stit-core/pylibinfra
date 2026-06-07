import asyncio

from aiogram.types import PreCheckoutQuery, User

from pylibinfra.tgbotkit import ChargeGuard, answer_pre_checkout, build_invoice

from .conftest import FakeRedis


class FakeBot:
    def __init__(self) -> None:
        self.answered: tuple[str, bool, str | None] | None = None

    async def answer_pre_checkout_query(
        self, pre_checkout_query_id: str, ok: bool, error_message: str | None = None
    ) -> bool:
        self.answered = (pre_checkout_query_id, ok, error_message)
        return True


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
