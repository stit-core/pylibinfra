from __future__ import annotations

import asyncio
import base64
import contextlib
import random
from collections.abc import Callable
from time import perf_counter

import pytest
from argon2.low_level import ARGON2_VERSION, Type, hash_secret
from pydantic import ValidationError

from pylibinfra.authnkit import (
    MAX_COST_KIB_PASSES,
    MAX_SALT_LEN,
    MAX_THREADS,
    MAX_TIME,
    MIN_SALT_LEN,
    Argon2idHasher,
    Argon2idParams,
    ConfigError,
    Hasher,
    HashParamsError,
    InvalidHashError,
)

FAST = {"time": 1, "memory_kib": 8192, "threads": 1, "key_len": 32, "salt_len": 16}

GOLDEN_SALT = b"0123456789abcdef"
GOLDEN_HASH = (
    "$argon2id$v=19$m=8192,t=3,p=4$MDEyMzQ1Njc4OWFiY2RlZg"
    "$3z8SyEOuzAHy+rYnoanyLsV68Qh7hLGc8D7UI+qpiRs"
)


def _params(**overrides: int) -> Argon2idParams:
    return Argon2idParams(**{**FAST, **overrides})


def _hasher(**overrides: int) -> Argon2idHasher:
    return Argon2idHasher(_params(**overrides))


def _b64(size: int) -> str:
    return base64.b64encode(bytes(size)).decode().rstrip("=")


def _phc(params: str, salt_len: int = 16, hash_len: int = 32) -> str:
    return f"$argon2id$v=19${params}${_b64(salt_len)}${_b64(hash_len)}"


_MUTATION_ALPHABET = "$=,.-_/+abzABZ019 \t\r\n\x00\xff€"


def _mutations(seeds: list[str], *, per_seed: int, seed: int) -> list[str]:
    """Детерминированный корпус мутантов: замена, удаление и вставка символов."""
    rng = random.Random(seed)
    corpus = list(seeds)
    for original in seeds:
        for _ in range(per_seed):
            chars = list(original)
            for _ in range(rng.randint(1, 4)):
                if not chars:
                    chars = [rng.choice(_MUTATION_ALPHABET)]
                    continue
                index = rng.randrange(len(chars))
                choice = rng.randint(0, 2)
                if choice == 0:
                    chars[index] = rng.choice(_MUTATION_ALPHABET)
                elif choice == 1:
                    del chars[index]
                else:
                    chars.insert(index, rng.choice(_MUTATION_ALPHABET))
            corpus.append("".join(chars))
    return corpus


def _non_canonical_tail(encoded: str, field: int) -> str:
    parts = encoded.split("$")
    parts[field] = parts[field][:-1] + "B"
    return "$".join(parts)


async def test_hash_verify_roundtrip() -> None:
    h = _hasher()
    encoded = await h.hash("abc")

    assert encoded.startswith("$argon2id$v=19$m=8192,t=1,p=1$")
    assert await h.verify("abc", encoded) is True
    assert await h.verify("abd", encoded) is False


async def test_hash_salt_is_random() -> None:
    h = _hasher()
    assert await h.hash("abc") != await h.hash("abc")


async def test_hash_accepts_non_ascii_password() -> None:
    h = _hasher()
    encoded = await h.hash("пароль-ёж")
    assert await h.verify("пароль-ёж", encoded) is True


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"time": 0}, id="t=0"),
        pytest.param({"time": MAX_TIME + 1}, id="t above window"),
        pytest.param({"threads": 0}, id="p=0"),
        pytest.param({"threads": MAX_THREADS + 1}, id="p above window"),
        pytest.param({"memory_kib": 1024}, id="m too small"),
        pytest.param({"memory_kib": 2 * 1024 * 1024}, id="m too large"),
        pytest.param({"time": 16, "memory_kib": 64 * 1024}, id="cost above window"),
        pytest.param({"key_len": 8}, id="short key"),
        pytest.param({"key_len": 256}, id="long key"),
        pytest.param({"salt_len": 4}, id="short salt"),
        pytest.param({"salt_len": MAX_SALT_LEN + 1}, id="long salt"),
    ],
)
def test_params_outside_window_are_rejected(overrides: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        _params(**overrides)

    # model_construct обходит pydantic — конструктор хэшера всё равно не пускает.
    raw = Argon2idParams.model_construct(**{**FAST, **overrides})
    with pytest.raises(ConfigError):
        Argon2idHasher(raw)


def test_cost_ceiling() -> None:
    at_ceiling = _params(time=MAX_TIME, memory_kib=MAX_COST_KIB_PASSES // MAX_TIME)
    assert at_ceiling.time * at_ceiling.memory_kib == MAX_COST_KIB_PASSES

    with pytest.raises(ValidationError):
        _params(time=MAX_TIME, memory_kib=MAX_COST_KIB_PASSES // MAX_TIME + 1024)

    # Дефолты кита обязаны быть внутри окна.
    assert Argon2idParams().time == 3


@pytest.mark.parametrize(
    "encoded",
    [
        pytest.param(_phc("m=8192,t=0,p=1"), id="t=0"),
        pytest.param(_phc("m=8192,t=65,p=1"), id="t above window"),
        pytest.param(_phc("m=8192,t=4294967295,p=1"), id="huge t"),
        pytest.param(_phc("m=8192,t=99999999999,p=1"), id="t overflow"),
        pytest.param(_phc("m=8192,t=1,p=0"), id="p=0"),
        pytest.param(_phc("m=8192,t=1,p=17"), id="p above window"),
        pytest.param(_phc("m=8192,t=1,p=300"), id="p overflow"),
        pytest.param(_phc("m=8192,t=1,p=1", hash_len=0), id="empty hash"),
        pytest.param(_phc("m=8192,t=1,p=1", hash_len=8), id="short hash"),
        pytest.param(_phc("m=8192,t=1,p=1", hash_len=129), id="huge hash"),
        pytest.param(_phc("m=4294967295,t=1,p=1"), id="huge m"),
        pytest.param(_phc("m=99999999999,t=1,p=1"), id="m overflow"),
        pytest.param(_phc("m=64,t=1,p=1"), id="m below window"),
        pytest.param(_phc("m=1048576,t=64,p=4"), id="cost above window"),
        pytest.param(_phc("m=8192,t=-1,p=1"), id="negative t"),
        pytest.param(_phc("m=8192,t=1,p=1", salt_len=4), id="short salt"),
        pytest.param(_phc("m=8192,t=1,p=1", salt_len=65), id="long salt"),
        pytest.param(_phc("m=8192,t=1,p=1").replace("v=19", "v=16"), id="wrong version"),
        pytest.param(_phc("m=8192,t=1,p=1").replace("v=19", "x=19"), id="bad version field"),
        pytest.param(_phc("m=8192,t=1,p=1").replace("v=19", "v=abc"), id="non-numeric version"),
        pytest.param(_phc("m=8192,t=1"), id="missing param"),
        pytest.param(_phc("t=1,m=8192,p=1"), id="reordered params"),
        pytest.param(_phc("m=008192,t=1,p=1"), id="leading zero in m"),
        pytest.param(_phc("m=8192,t=01,p=1"), id="leading zero in t"),
        pytest.param(_phc("m=8192,t=1,p=+1"), id="plus sign in p"),
        pytest.param(_phc("m=8192,t=1,p=1x"), id="trailing garbage in p"),
        pytest.param(_phc("m=8192,t=1,p=1").replace("argon2id", "argon2i"), id="not argon2id"),
        pytest.param("$argon2id$v=19$m=8192,t=1,p=1$***$***", id="bad base64"),
        pytest.param(f"$argon2id$v=19$m=8192,t=1,p=1${'A' * 22}=={_b64(32)}", id="padded salt"),
        pytest.param(_non_canonical_tail(_phc("m=8192,t=1,p=1"), 4), id="non-canonical salt"),
        pytest.param(_non_canonical_tail(_phc("m=8192,t=1,p=1"), 5), id="non-canonical hash"),
        pytest.param(f"$argon2id$v=19$m=8192,t=1,p=1$AAAA\nAAAAAAAA${_b64(32)}", id="newline"),
        pytest.param(" " + _phc("m=8192,t=1,p=1"), id="leading space"),
        pytest.param("x" + _phc("m=8192,t=1,p=1"), id="leading garbage"),
        pytest.param("notahash", id="garbage"),
        pytest.param("", id="empty"),
    ],
)
async def test_verify_rejects_hostile_hash(encoded: str) -> None:
    h = _hasher()
    with pytest.raises(InvalidHashError):
        await h.verify("abc", encoded)


async def test_phc_string_matches_the_reference_encoder() -> None:
    # PHC — межъязыковой контракт: строку, выданную stit-сервисом на другом
    # стеке, обязан принимать этот кит, и наоборот.
    reference = hash_secret(
        b"s3cret",
        GOLDEN_SALT,
        time_cost=3,
        memory_cost=8192,
        parallelism=4,
        hash_len=32,
        type=Type.ID,
        version=ARGON2_VERSION,
    ).decode()
    assert reference == GOLDEN_HASH

    h = _hasher(time=3, memory_kib=8192, threads=4, key_len=32, salt_len=16)
    assert await h.verify("s3cret", GOLDEN_HASH) is True
    assert await h.verify("wrong", GOLDEN_HASH) is False
    assert h.needs_rehash(GOLDEN_HASH) is False


async def test_verify_accepts_canonical_foreign_hash() -> None:
    # Каноническая чужая строка внутри окна — это «пароль не совпал», не ошибка.
    h = _hasher()
    assert await h.verify("abc", _phc("m=8192,t=1,p=1")) is False


async def test_hash_params_error_is_distinguishable() -> None:
    h = _hasher()

    with pytest.raises(HashParamsError):
        await h.verify("abc", _phc("m=8192,t=65,p=1"))

    with pytest.raises(InvalidHashError) as unparsable:
        await h.verify("abc", "notahash")
    assert not isinstance(unparsable.value, HashParamsError)

    assert h.needs_rehash(_phc("m=8192,t=65,p=1")) is True


@pytest.mark.parametrize(
    "encoded",
    [
        pytest.param(_phc("m=1048576,t=2147483648,p=1"), id="huge t"),
        pytest.param(_phc("m=1048576,t=64,p=4"), id="cost above window"),
    ],
)
async def test_verify_out_of_window_costs_nothing(encoded: str) -> None:
    # Окно проверяется до вычисления: честный расчёт занял бы секунды и гигабайты.
    h = _hasher()
    start = perf_counter()
    with pytest.raises(InvalidHashError):
        await h.verify("abc", encoded)
    assert perf_counter() - start < 2.0


@pytest.mark.parametrize("field", [4, 5])
async def test_non_canonical_base64_is_rejected(field: int) -> None:
    h = _hasher()
    spoiled = _non_canonical_tail(_phc("m=8192,t=1,p=1"), field)

    with pytest.raises(InvalidHashError):
        await h.verify("abc", spoiled)
    assert h.needs_rehash(spoiled) is True


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param({"time": 2}, id="time"),
        pytest.param({"memory_kib": 16384}, id="memory"),
        pytest.param({"threads": 2}, id="threads"),
        pytest.param({"key_len": 64}, id="keylen"),
        pytest.param({"salt_len": 32}, id="saltlen"),
    ],
)
async def test_needs_rehash_on_changed_params(changed: dict[str, int]) -> None:
    h = _hasher()
    encoded = await h.hash("abc")
    assert h.needs_rehash(encoded) is False

    other = _hasher(**changed)
    assert other.needs_rehash(encoded) is True
    # Старый хэш при этом проверяется на своих параметрах.
    assert await other.verify("abc", encoded) is True


async def test_verify_rejects_mutated_hashes_with_kit_errors() -> None:
    # Аналог FuzzArgon2idVerify: PHC-строка приходит из хранилища, и любой её
    # мутант обязан давать `InvalidHashError`, а не ошибку библиотеки.
    # Соль фиксирована: на случайной seed-строка меняется от прогона к прогону,
    # вместе с ней весь корпус мутантов — и падение невоспроизводимо.
    h = _hasher()
    good = hash_secret(
        b"any",
        GOLDEN_SALT,
        time_cost=FAST["time"],
        memory_cost=FAST["memory_kib"],
        parallelism=FAST["threads"],
        hash_len=FAST["key_len"],
        type=Type.ID,
        version=ARGON2_VERSION,
    ).decode()
    seeds = [
        "",
        "not-a-hash",
        good,
        "$argon2id$v=19$m=65536,t=3,p=4$invalid$invalid",
        _phc("m=8192,t=1,p=1"),
    ]

    for encoded in _mutations(seeds, per_seed=40, seed=1234):
        with contextlib.suppress(InvalidHashError):
            assert await h.verify("any", encoded) is (encoded == good)


async def test_verify_dummy_costs_the_same_as_verify() -> None:
    for salt_len in (MIN_SALT_LEN, 16, MAX_SALT_LEN):
        h = _hasher(salt_len=salt_len)
        encoded = await h.hash("abc")

        start = perf_counter()
        await h.verify("abc", encoded)
        verify = perf_counter() - start

        start = perf_counter()
        await h.verify_dummy()
        dummy = perf_counter() - start

        # Запас на шум загруженной машины: no-op отличался бы на три порядка.
        assert dummy > verify / 10


async def test_hash_does_not_block_the_loop() -> None:
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    before = ticks
    try:
        await _hasher().hash("abc")
    finally:
        task.cancel()
    assert ticks > before


async def test_hasher_satisfies_port() -> None:
    h: Hasher = _hasher()
    assert isinstance(h, Hasher)


async def test_metrics(read_metric: Callable[..., float]) -> None:
    h = _hasher()
    encoded = await h.hash("abc")
    await h.verify("abc", encoded)
    await h.verify_dummy()
    with pytest.raises(InvalidHashError):
        await h.verify("abc", "garbage")

    assert read_metric("authn_password_hash_duration_seconds") == 1
    # Гистограмма пишется на каждый вызов, включая нечитаемую строку.
    assert read_metric("authn_password_verify_duration_seconds") == 3
