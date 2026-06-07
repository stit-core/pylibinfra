import pyotp

from pylibinfra.authnkit import TOTPGenerator


def test_generate_and_verify() -> None:
    gen = TOTPGenerator("TestIssuer")
    secret, url = gen.generate("user@example.com")
    assert secret != ""
    assert url != ""

    code = pyotp.TOTP(secret).now()
    assert gen.verify(secret, code) is True


def test_invalid_code() -> None:
    gen = TOTPGenerator("TestIssuer")
    secret, _ = gen.generate("user@example.com")
    assert gen.verify(secret, "000000") is False
