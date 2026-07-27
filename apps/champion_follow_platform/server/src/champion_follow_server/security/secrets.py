import hashlib
import hmac
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretDigester:
    def __init__(self, pepper: bytes) -> None:
        if len(pepper) < 32:
            raise ValueError("pepper must contain at least 32 bytes")
        self._pepper = pepper

    def digest(self, plaintext: str) -> bytes:
        return hmac.new(
            self._pepper, plaintext.encode("utf-8"), hashlib.sha256
        ).digest()

    def matches(self, stored: bytes, plaintext: str) -> bool:
        return hmac.compare_digest(stored, self.digest(plaintext))

    @staticmethod
    def generate_token() -> str:
        return secrets.token_urlsafe(32)


class SecretVault:
    VERSION = b"\x01"

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("vault key must contain exactly 32 bytes")
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext, self.VERSION)
        return self.VERSION + nonce + ciphertext

    def decrypt(self, packed: bytes) -> bytes:
        if len(packed) < 30 or packed[:1] != self.VERSION:
            raise ValueError("unsupported encrypted value")
        nonce = packed[1:13]
        return self._cipher.decrypt(nonce, packed[13:], self.VERSION)
