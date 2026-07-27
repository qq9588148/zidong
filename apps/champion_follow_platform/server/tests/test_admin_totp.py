from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest

from champion_follow_server.services.admin_bootstrap import (
    AdminAlreadyExists,
    AdminBootstrapService,
)


@pytest.mark.asyncio
async def test_bootstrap_creates_one_admin_and_requires_current_totp(
    db_session, password_hasher, secret_vault
) -> None:
    service = AdminBootstrapService(password_hasher, secret_vault)
    result = await service.create_pending_admin(
        db_session,
        username="owner",
        password="test-admin-password-with-16-chars",
        issuer="Champion Follow",
    )
    seed = parse_qs(urlparse(result.provisioning_uri).query)["secret"][0]
    timestamp = 1_785_136_800
    otp = pyotp.TOTP(seed).at(timestamp)

    await service.confirm_totp(
        db_session,
        result.account_id,
        otp,
        now=datetime.fromtimestamp(timestamp, UTC),
    )
    with pytest.raises(AdminAlreadyExists):
        await service.create_pending_admin(
            db_session,
            username="owner-2",
            password="another-test-admin-password",
            issuer="Champion Follow",
        )
