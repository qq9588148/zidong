import base64
import hashlib
from uuid import UUID

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


class InvalidDeviceProof(ValueError):
    pass


def _decode_bounded(value: str, minimum: int, maximum: int) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidDeviceProof("invalid device proof") from exc
    if not minimum <= len(decoded) <= maximum:
        raise InvalidDeviceProof("invalid device proof")
    return decoded


def enrollment_message(challenge_id: UUID, nonce: bytes) -> bytes:
    if len(nonce) != 32:
        raise InvalidDeviceProof("invalid device proof")
    return (
        b"champion-follow-device-bind-v1\x00"
        + challenge_id.bytes
        + b"\x00"
        + nonce
    )


def device_login_message(challenge_id: UUID, nonce: bytes) -> bytes:
    if len(nonce) != 32:
        raise InvalidDeviceProof("invalid device proof")
    return (
        b"champion-follow-device-login-v1\x00"
        + challenge_id.bytes
        + b"\x00"
        + nonce
    )


def _canonical_p256_public_key(spki_der: bytes):
    public_key = serialization.load_der_public_key(spki_der)
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise InvalidDeviceProof("invalid device proof")
    if not isinstance(public_key.curve, ec.SECP256R1):
        raise InvalidDeviceProof("invalid device proof")
    canonical = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if canonical != spki_der:
        raise InvalidDeviceProof("invalid device proof")
    return public_key


def verify_bound_device_signature(
    *, public_key_spki_der: bytes, proof_der_b64: str, message: bytes
) -> None:
    if not 80 <= len(public_key_spki_der) <= 256:
        raise InvalidDeviceProof("invalid device proof")
    signature = _decode_bounded(proof_der_b64, 64, 80)
    try:
        public_key = _canonical_p256_public_key(public_key_spki_der)
        public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    except (
        InvalidDeviceProof,
        InvalidSignature,
        UnsupportedAlgorithm,
        ValueError,
        TypeError,
    ) as exc:
        raise InvalidDeviceProof("invalid device proof") from exc


def verify_device_proof(
    *,
    challenge_id: UUID,
    nonce: bytes,
    public_key_spki_der_b64: str,
    proof_der_b64: str,
) -> tuple[bytes, bytes]:
    spki_der = _decode_bounded(public_key_spki_der_b64, 80, 256)
    signature = _decode_bounded(proof_der_b64, 64, 80)
    try:
        public_key = _canonical_p256_public_key(spki_der)
        public_key.verify(
            signature,
            enrollment_message(challenge_id, nonce),
            ec.ECDSA(hashes.SHA256()),
        )
    except (
        InvalidDeviceProof,
        InvalidSignature,
        UnsupportedAlgorithm,
        ValueError,
        TypeError,
    ) as exc:
        raise InvalidDeviceProof("invalid device proof") from exc
    return spki_der, hashlib.sha256(spki_der).digest()
