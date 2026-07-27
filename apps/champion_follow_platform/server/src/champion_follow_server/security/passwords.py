from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class PasswordHasher:
    def __init__(self) -> None:
        self._hasher = Argon2Hasher(
            time_cost=3,
            memory_cost=65_536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
        )

    def hash(self, plaintext: str) -> str:
        return self._hasher.hash(plaintext)

    def verify(self, encoded: str, plaintext: str) -> bool:
        try:
            return self._hasher.verify(encoded, plaintext)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, encoded: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(encoded)
        except InvalidHashError:
            return True
