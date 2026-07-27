import base64

import pytest

from champion_follow_server.models.auth import CodePurpose, DeviceStatus
from champion_follow_server.schemas.auth import RegistrationRequest
from champion_follow_server.security.device_keys import enrollment_message
from champion_follow_server.services.device_binding import InvalidEnrollment


@pytest.mark.asyncio
async def test_registration_consumes_code_and_binds_verified_key(
    db_session,
    registration_code,
    fake_device_keypair,
    binding_service,
    clock,
) -> None:
    challenge = await binding_service.create_challenge(
        db_session, registration_code.plaintext
    )
    signature = base64.b64encode(
        fake_device_keypair.private_key.sign(
            enrollment_message(challenge.id, challenge.nonce)
        )
    ).decode("ascii")
    result = await binding_service.register(
        db_session,
        code_plaintext=registration_code.plaintext,
        challenge_id=challenge.id,
        username="licensed-user",
        password="test-user-password-with-16-chars",
        public_key_spki_der_b64=fake_device_keypair.public_key_spki_der_b64,
        proof_der_b64=signature,
    )

    assert result.device.account_id == result.account.id
    assert result.device.public_key_spki_der == (
        fake_device_keypair.public_key_spki_der
    )


@pytest.mark.asyncio
async def test_registration_rejects_signature_from_other_key(
    db_session,
    registration_code,
    fake_device_keypair,
    another_device_keypair,
    binding_service,
) -> None:
    challenge = await binding_service.create_challenge(
        db_session, registration_code.plaintext
    )
    wrong = base64.b64encode(
        another_device_keypair.private_key.sign(
            enrollment_message(challenge.id, challenge.nonce)
        )
    ).decode("ascii")

    with pytest.raises(InvalidEnrollment):
        await binding_service.register(
            db_session,
            code_plaintext=registration_code.plaintext,
            challenge_id=challenge.id,
            username="licensed-user",
            password="test-user-password-with-16-chars",
            public_key_spki_der_b64=fake_device_keypair.public_key_spki_der_b64,
            proof_der_b64=wrong,
        )


@pytest.mark.asyncio
async def test_rebind_unbinds_old_device_and_advances_binding_epoch(
    db_session,
    admin_account,
    registration_code,
    fake_device_keypair,
    another_device_keypair,
    binding_service,
    authorization_code_service,
) -> None:
    first_challenge = await binding_service.create_challenge(
        db_session, registration_code.plaintext
    )
    first_proof = base64.b64encode(
        fake_device_keypair.private_key.sign(
            enrollment_message(first_challenge.id, first_challenge.nonce)
        )
    ).decode("ascii")
    first = await binding_service.register(
        db_session,
        code_plaintext=registration_code.plaintext,
        challenge_id=first_challenge.id,
        username="rebind-user",
        password="test-user-password-with-16-chars",
        public_key_spki_der_b64=fake_device_keypair.public_key_spki_der_b64,
        proof_der_b64=first_proof,
    )
    rebind_code = await authorization_code_service.issue(
        db_session,
        actor=admin_account,
        purpose=CodePurpose.REBIND,
        target_account_id=first.account.id,
        reason="replace licensed device",
        request_id="request-rebind",
    )
    second_challenge = await binding_service.create_challenge(
        db_session, rebind_code.plaintext
    )
    second_proof = base64.b64encode(
        another_device_keypair.private_key.sign(
            enrollment_message(second_challenge.id, second_challenge.nonce)
        )
    ).decode("ascii")

    second = await binding_service.rebind(
        db_session,
        code_plaintext=rebind_code.plaintext,
        challenge_id=second_challenge.id,
        username="rebind-user",
        password="test-user-password-with-16-chars",
        public_key_spki_der_b64=another_device_keypair.public_key_spki_der_b64,
        proof_der_b64=second_proof,
    )
    assert first.device.status == DeviceStatus.UNBOUND
    assert first.device.unbound_at is not None
    assert second.device.binding_epoch == 2


def test_registration_request_repr_hides_all_enrollment_secrets(
    fake_device_keypair,
) -> None:
    request = RegistrationRequest(
        authorization_code="CF1-" + "a" * 43,
        challenge_id="00000000-0000-0000-0000-000000000001",
        username="licensed-user",
        password="test-user-password-with-16-chars",
        public_key_spki_der=fake_device_keypair.public_key_spki_der_b64,
        proof_der="A" * 88,
    )
    rendered = repr(request)
    assert "test-user-password" not in rendered
    assert "authorization_code" not in rendered
    assert "proof_der" not in rendered
