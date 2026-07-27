from datetime import datetime, timedelta

import pyotp

from champion_follow_server.models.auth import Account, AdminTotp
from champion_follow_server.security.secrets import SecretVault


class TotpVerifier:
    def __init__(self, vault: SecretVault) -> None:
        self._vault = vault

    def verify(
        self,
        *,
        account: Account,
        totp: AdminTotp,
        code: str,
        now: datetime,
    ) -> bool:
        if account.locked_until is not None and account.locked_until > now:
            return False
        valid_shape = len(code) == 6 and code.isascii() and code.isdigit()
        secret = self._vault.decrypt(totp.secret_ciphertext).decode("ascii")
        valid = valid_shape and pyotp.TOTP(secret).verify(
            code, for_time=now, valid_window=1
        )
        if valid:
            account.failed_login_count = 0
            account.locked_until = None
            return True
        account.failed_login_count += 1
        if account.failed_login_count >= 5:
            account.locked_until = now + timedelta(minutes=15)
            account.failed_login_count = 0
        return False
