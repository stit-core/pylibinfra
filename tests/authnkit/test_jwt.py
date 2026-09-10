from __future__ import annotations

import contextlib
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt as pyjwt
import pytest
from pydantic import SecretStr, ValidationError

from pylibinfra.authnkit import (
    CLAIM_TOKEN_TYPE,
    DEFAULT_ACCESS_TTL,
    HS256JWT,
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    ConfigError,
    JWTConfig,
    JWTParser,
    JWTSigner,
    TokenAudienceError,
    TokenError,
    TokenExpiredError,
    TokenMalformedError,
    TokenSignatureError,
    WrongTokenTypeError,
)
from pylibinfra.clockkit import Fixed, Tickable

SECRET = "test-secret-key-of-at-least-32-bytes!!"
OTHER_SECRET = "other-secret-key-of-at-least-32-bytes!"
NOW = datetime.fromtimestamp(1_700_000_000, UTC)

_MUTATION_ALPHABET = ".-_abzABZ019 \t\r\n\x00\xff€"


def _cfg(**overrides: Any) -> JWTConfig:
    return JWTConfig(**{"secret": SECRET, **overrides})


def _jwt(**kwargs: Any) -> HS256JWT:
    cfg = kwargs.pop("cfg", None) or _cfg()
    return HS256JWT(cfg, **kwargs)


def _foreign_token(claims: dict[str, Any], secret: str = SECRET) -> str:
    return pyjwt.encode(claims, secret, algorithm="HS256")


def _mutations(seeds: list[str], *, per_seed: int, seed: int) -> list[str]:
    """Детерминированный корпус мутантов токена."""
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


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"secret": "short"}, id="short secret"),
        pytest.param({"secret": ""}, id="empty secret"),
        pytest.param({"leeway": -1}, id="negative leeway"),
        pytest.param({"access_ttl": -1}, id="negative access ttl"),
        pytest.param({"access_ttl": 0}, id="zero access ttl"),
        pytest.param({"refresh_ttl": -1}, id="negative refresh ttl"),
    ],
)
def test_config_rejects_invalid_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _cfg(**overrides)


def test_constructor_rejects_short_secret() -> None:
    # model_construct обходит pydantic — конструктор всё равно не пускает.
    raw = JWTConfig.model_construct(secret=SecretStr("short"), access_ttl=1.0)
    with pytest.raises(ConfigError):
        HS256JWT(raw)


def test_config_secret_is_not_leaked() -> None:
    cfg = _cfg()
    assert SECRET not in repr(cfg)
    assert SECRET not in str(cfg)

    # Ошибка соседнего поля не тянет за собой значение секрета.
    with pytest.raises(ValidationError) as exc:
        _cfg(leeway=-1)
    assert SECRET not in str(exc.value)


def test_sign_parse_roundtrip() -> None:
    h = _jwt()
    token = h.sign({"sub": "user-123", "role": "admin"}, 60)

    claims = h.parse(token)
    assert claims["sub"] == "user-123"
    assert claims["role"] == "admin"
    assert isinstance(claims["exp"], int)
    assert isinstance(claims["iat"], int)
    # Тип проставляют только sign_access/sign_refresh.
    assert CLAIM_TOKEN_TYPE not in claims


@pytest.mark.parametrize("ttl", [0, -1.0])
def test_sign_rejects_non_positive_ttl(ttl: float) -> None:
    with pytest.raises(ConfigError):
        _jwt().sign({"sub": "x"}, ttl)


def test_sign_access_and_refresh_use_config_ttl() -> None:
    clock = Fixed(NOW)
    h = _jwt(cfg=_cfg(access_ttl=300, refresh_ttl=172800), clock=clock)

    assert h.parse(h.sign_access({"sub": "u"}))["exp"] == int(NOW.timestamp()) + 300
    assert h.parse(h.sign_refresh({"sub": "u"}))["exp"] == int(NOW.timestamp()) + 172800

    default = _jwt(clock=clock)
    exp = default.parse(default.sign_access({"sub": "u"}))["exp"]
    assert exp == int(NOW.timestamp() + DEFAULT_ACCESS_TTL)


def test_token_type_is_checked() -> None:
    h = _jwt()
    access = h.sign_access({"sub": "u"})
    refresh = h.sign_refresh({"sub": "u"})

    assert h.parse_access(access)[CLAIM_TOKEN_TYPE] == TOKEN_TYPE_ACCESS
    assert h.parse_refresh(refresh)[CLAIM_TOKEN_TYPE] == TOKEN_TYPE_REFRESH

    with pytest.raises(WrongTokenTypeError):
        h.parse_access(refresh)
    with pytest.raises(WrongTokenTypeError):
        h.parse_refresh(access)

    # Подмена typ вызывающим перетирается подписывающей стороной.
    forged = h.sign_access({"sub": "u", CLAIM_TOKEN_TYPE: TOKEN_TYPE_REFRESH})
    assert h.parse_access(forged)[CLAIM_TOKEN_TYPE] == TOKEN_TYPE_ACCESS

    untyped = h.sign({"sub": "u"}, 60)
    with pytest.raises(WrongTokenTypeError):
        h.parse_access(untyped)
    assert h.parse(untyped)["sub"] == "u"


def test_expired_token() -> None:
    clock = Tickable(NOW)
    h = _jwt(clock=clock)
    token = h.sign({"sub": "x"}, 60)

    clock.advance(timedelta(minutes=3))
    with pytest.raises(TokenExpiredError):
        h.parse(token)


def test_leeway() -> None:
    clock = Tickable(NOW)
    token = _jwt(clock=clock).sign({"sub": "x"}, 60)
    clock.advance(90)

    assert _jwt(cfg=_cfg(leeway=60), clock=clock).parse(token)["sub"] == "x"
    with pytest.raises(TokenExpiredError):
        _jwt(cfg=_cfg(leeway=1), clock=clock).parse(token)


def test_wrong_signature() -> None:
    token = _jwt().sign({"sub": "x"}, 60)
    with pytest.raises(TokenSignatureError):
        _jwt(cfg=_cfg(secret=OTHER_SECRET)).parse(token)


@pytest.mark.parametrize("garbage", ["", "not.a.token", "only-one-part", "a.b.c"])
def test_malformed_token(garbage: str) -> None:
    with pytest.raises(TokenMalformedError):
        _jwt().parse(garbage)


def test_rejects_alg_none() -> None:
    header = pyjwt.utils.base64url_encode(b'{"alg":"none","typ":"JWT"}').decode()
    payload = pyjwt.utils.base64url_encode(b'{"sub":"x","exp":9999999999}').decode()
    h = _jwt()
    for token in (f"{header}.{payload}.", f"{header}.{payload}"):
        with pytest.raises(TokenMalformedError):
            h.parse(token)


def test_rejects_other_algorithms() -> None:
    h = _jwt()
    claims = {"sub": "x", "exp": 9999999999}
    with pytest.raises(TokenMalformedError):
        h.parse(pyjwt.encode(claims, "k" * 64, algorithm="HS512"))
    with pytest.raises(TokenMalformedError):
        h.parse(pyjwt.encode(claims, "k" * 64, algorithm="HS384"))


def test_requires_exp() -> None:
    with pytest.raises(TokenMalformedError):
        _jwt().parse(_foreign_token({"sub": "x"}))


@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({"sub": "x", "exp": 9999999999, "nbf": 9999999999}, id="nbf in future"),
        pytest.param({"sub": "x", "exp": 9999999999, "iat": 9999999999}, id="iat in future"),
        pytest.param({"sub": "x", "exp": "soon"}, id="string exp"),
        pytest.param({"sub": "x", "exp": True}, id="bool exp"),
        pytest.param({"sub": 123, "exp": 9999999999}, id="non-string sub"),
    ],
)
def test_rejects_bad_claims(claims: dict[str, Any]) -> None:
    with pytest.raises(TokenMalformedError):
        _jwt().parse(_foreign_token(claims))


def test_rejects_non_object_payload() -> None:
    token = pyjwt.api_jws.encode(b"[1,2,3]", SECRET, algorithm="HS256")
    with pytest.raises(TokenMalformedError):
        _jwt().parse(token)


def test_sign_rejects_unserializable_claims() -> None:
    with pytest.raises(TypeError):
        _jwt().sign({"c": object()}, 60)


def test_issuer_and_audience() -> None:
    issued = _jwt(issuer="issuer-a", audience="svc-a")
    token = issued.sign_access({"sub": "u"})

    claims = issued.parse_access(token)
    assert claims["iss"] == "issuer-a"
    assert claims["aud"] == "svc-a"

    with pytest.raises(TokenAudienceError):
        _jwt(audience="svc-b").parse(token)
    with pytest.raises(TokenAudienceError):
        _jwt(issuer="issuer-b").parse(token)

    # Без ожиданий iss/aud не проверяются.
    assert _jwt().parse(token)["sub"] == "u"


def test_audience_list_intersection() -> None:
    multi = _jwt(audience=["svc-a", "svc-b"])
    token = multi.sign_access({"sub": "u"})
    assert multi.parse(token)["aud"] == ["svc-a", "svc-b"]

    assert _jwt(audience="svc-b").parse(token)["sub"] == "u"
    with pytest.raises(TokenAudienceError):
        _jwt(audience="svc-c").parse(token)


@pytest.mark.parametrize(
    "aud",
    [
        pytest.param(None, id="missing"),
        pytest.param([[1]], id="unhashable element"),
        pytest.param(42, id="number"),
    ],
)
def test_hostile_audience_claim(aud: Any) -> None:
    claims: dict[str, Any] = {"sub": "u", "exp": 9999999999}
    if aud is not None:
        claims["aud"] = aud
    with pytest.raises(TokenAudienceError):
        _jwt(audience="svc-a").parse(_foreign_token(claims))


def test_parse_rejects_mutated_tokens_with_kit_errors() -> None:
    # Аналог FuzzJWTParse: токен приходит от клиента, и любой его мутант обязан
    # давать `TokenError`, а не ошибку pyjwt или разбора base64.
    # Часы фиксированы: на реальных `iat`/`exp` seed-токена меняются от прогона к
    # прогону, вместе с ними весь корпус мутантов — и падение невоспроизводимо.
    h = _jwt(clock=Fixed(NOW))
    valid = h.sign({"sub": "x"}, 3600)
    seeds = [
        "",
        "not.a.token",
        "aaa.bbb.ccc",
        valid,
        "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJ4IiwiZXhwIjo5OTk5OTk5OTk5fQ.",
    ]

    for token in _mutations(seeds, per_seed=60, seed=4321):
        with contextlib.suppress(TokenError):
            assert h.parse(token)["sub"] == "x"


def test_ports_are_satisfied() -> None:
    h = _jwt()
    signer: JWTSigner = h
    parser: JWTParser = h
    assert isinstance(signer, JWTSigner)
    assert isinstance(parser, JWTParser)


def test_metrics(read_metric: Callable[..., float]) -> None:
    h = _jwt(issuer="issuer-a", clock=Fixed(NOW))
    token = h.sign_access({"sub": "u"})

    h.parse_access(token)
    with pytest.raises(TokenMalformedError):
        h.parse("not.a.token")
    with pytest.raises(WrongTokenTypeError):
        h.parse_refresh(token)
    with pytest.raises(TokenSignatureError):
        h.parse(_foreign_token({"sub": "u", "exp": 9999999999}, OTHER_SECRET))
    with pytest.raises(TokenAudienceError):
        h.parse(_foreign_token({"sub": "u", "exp": 9999999999, "iss": "issuer-b"}))
    with pytest.raises(TokenExpiredError):
        h.parse(_foreign_token({"sub": "u", "exp": 1, "iss": "issuer-a"}))

    metric = "authn_jwt_verifications_total"
    for result in ("ok", "malformed", "wrong_type", "signature_invalid", "audience_mismatch"):
        assert read_metric(metric, {"result": result}) == 1, result
    assert read_metric(metric, {"result": "expired"}) == 1
