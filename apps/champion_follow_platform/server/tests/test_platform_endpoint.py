import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from champion_follow_server.security.task_signing import (
    TaskSigner,
    canonical_task_bytes,
)
from champion_follow_server.services.audit import AuditWriter
from champion_follow_server.services.platform_endpoints import (
    PlatformEndpointService,
    public_envelope,
)


@pytest.mark.asyncio
async def test_platform_endpoint_is_https_signed_versioned_and_audited(
    db_session, admin_account, clock
) -> None:
    signer = TaskSigner(
        Ed25519PrivateKey.from_private_bytes(bytes(range(32))), "test-v1"
    )
    service = PlatformEndpointService(signer, AuditWriter(), clock)
    row = await service.update(
        db_session,
        actor_account_id=admin_account.id,
        entry_url="https://ng1z.com/",
        reason="test endpoint update",
        request_id="endpoint-test-1",
    )
    envelope = public_envelope(row)
    signature = base64.urlsafe_b64decode(envelope.pop("signature"))
    signer.public_key.verify(signature, canonical_task_bytes(envelope))
    assert envelope["config_version"] == 2
    assert envelope["entry_url"] == "https://ng1z.com/"
    assert envelope["allowed_origins"] == ["https://ng1z.com"]


@pytest.mark.asyncio
async def test_platform_endpoint_rejects_credentials_and_non_https(
    db_session, admin_account, clock
) -> None:
    signer = TaskSigner(
        Ed25519PrivateKey.from_private_bytes(bytes(range(32))), "test-v1"
    )
    service = PlatformEndpointService(signer, AuditWriter(), clock)
    for invalid in ("http://ng1z.com/", "https://user:pass@ng1z.com/"):
        with pytest.raises(ValueError):
            await service.update(
                db_session,
                actor_account_id=admin_account.id,
                entry_url=invalid,
                reason="test invalid endpoint",
                request_id="endpoint-test-2",
            )
