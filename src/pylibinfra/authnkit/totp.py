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
        ok = pyotp.TOTP(secret).verify(code)
        record_totp_result("ok" if ok else "invalid")
        return ok
