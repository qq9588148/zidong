import base64
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from champion_follow_server.security.device_keys import (
    InvalidDeviceProof,
    enrollment_message,
    verify_device_proof,
)
from champion_follow_server.security.task_signing import (
    TaskSigner,
    canonical_task_bytes,
)


def test_signature_covers_revision_and_action() -> None:
    signer = TaskSigner(
        Ed25519PrivateKey.from_private_bytes(bytes(range(32))), "test-v1"
    )
    envelope = {
        "device_id": "00000000-0000-0000-0000-000000000001",
        "period_id": "2607270001",
        "revision": 4,
        "action": "CANCEL",
        "expires_at": "2026-07-27T12:00:00Z",
        "payload": {"reason": "champion_withdrew"},
        "signing_key_version": "test-v1",
    }
    signature = signer.sign(envelope)
    signer.public_key.verify(signature, canonical_task_bytes(envelope))

    envelope["revision"] = 3
    with pytest.raises(Exception):
        signer.public_key.verify(signature, canonical_task_bytes(envelope))


def test_device_proof_accepts_only_the_bound_p256_key() -> None:
    challenge_id = UUID("00000000-0000-0000-0000-000000000123")
    nonce = bytes(range(32))
    private_key = ec.derive_private_key(7, ec.SECP256R1())
    spki = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    proof = private_key.sign(
        enrollment_message(challenge_id, nonce), ec.ECDSA(hashes.SHA256())
    )

    canonical, fingerprint = verify_device_proof(
        challenge_id=challenge_id,
        nonce=nonce,
        public_key_spki_der_b64=base64.b64encode(spki).decode("ascii"),
        proof_der_b64=base64.b64encode(proof).decode("ascii"),
    )
    assert canonical == spki
    assert len(fingerprint) == 32

    with pytest.raises(InvalidDeviceProof, match="^invalid device proof$"):
        verify_device_proof(
            challenge_id=challenge_id,
            nonce=b"x" * 32,
            public_key_spki_der_b64=base64.b64encode(spki).decode("ascii"),
            proof_der_b64=base64.b64encode(proof).decode("ascii"),
        )
