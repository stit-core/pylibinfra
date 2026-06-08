"""TOTP-генератор (RFC 6238) поверх pyotp."""

import pyotp

from .metrics import record_totp_result


class TOTPGenerator:
    def __init__(self, issuer: str) -> None:
        self.issuer = issuer

    def generate(self, account_name: str) -> tuple[str, str]:
        secret = pyotp.random_base32()
        otpauth_url = pyotp.TOTP(secret).provisioning_uri(
            name=account_name, issuer_name=self.issuer
        )
        return secret, otpauth_url

    def verify(self, secret: str, code: str) -> bool:
        # valid_window=1: принять код предыдущего/следующего 30с-окна — допуск на рассинхрон
        # часов клиента и сервера; 0 ронял бы валидный код у границы окна.
        ok = pyotp.TOTP(secret).verify(code, valid_window=1)
        record_totp_result("ok" if ok else "invalid")
        return ok
