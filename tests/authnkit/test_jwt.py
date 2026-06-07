import pytest

from pylibinfra.authnkit import (
    HS256JWT,
    ErrTokenExpired,
    ErrTokenMalformed,
    ErrTokenSignature,
    JWTConfig,
)


def _jwt(secret: str = "test-secret-key") -> HS256JWT:
    return HS256JWT(JWTConfig(JWT_SECRET=secret))


def test_sign_parse_roundtrip() -> None:
    h = _jwt()
    tok = h.sign({"sub": "user-123", "role": "admin"}, 60)
    assert tok != ""

    parsed = h.parse(tok)
    assert parsed["sub"] == "user-123"
    assert parsed["role"] == "admin"
    assert "exp" in parsed
    assert "iat" in parsed


def test_expired_token() -> None:
    h = _jwt()
    tok = h.sign({"sub": "x"}, -1)
    with pytest.raises(ErrTokenExpired):
        h.parse(tok)


def test_wrong_signature() -> None:
    tok = _jwt().sign({"sub": "x"}, 60)
    with pytest.raises(ErrTokenSignature):
        _jwt("other-secret").parse(tok)


@pytest.mark.parametrize("garbage", ["", "not.a.token", "only-one-part"])
def test_malformed_token(garbage: str) -> None:
    h = _jwt()
    with pytest.raises(ErrTokenMalformed):
        h.parse(garbage)
