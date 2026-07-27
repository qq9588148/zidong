import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


class FakeDevicePrivateKey:
    def __init__(self, key: ec.EllipticCurvePrivateKey) -> None:
        self._key = key

    def sign(self, value: bytes) -> bytes:
        return self._key.sign(value, ec.ECDSA(hashes.SHA256()))


@dataclass(frozen=True, slots=True)
class FakeDeviceKeyPair:
    private_key: FakeDevicePrivateKey
    public_key_spki_der: bytes
    public_key_spki_der_b64: str


def make_device_keypair(private_value: int) -> FakeDeviceKeyPair:
    key = ec.derive_private_key(private_value, ec.SECP256R1())
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return FakeDeviceKeyPair(
        private_key=FakeDevicePrivateKey(key),
        public_key_spki_der=spki,
        public_key_spki_der_b64=base64.b64encode(spki).decode("ascii"),
    )
