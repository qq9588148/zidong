from champion_follow_server.security.passwords import PasswordHasher
from champion_follow_server.security.secrets import SecretDigester, SecretVault


def test_password_hash_and_secret_digest_do_not_contain_plaintext() -> None:
    password = "test-password-not-used-by-any-account"
    password_hash = PasswordHasher().hash(password)
    digester = SecretDigester(b"test-only-pepper-with-more-than-32-bytes")
    digest = digester.digest("CF1-test-code-with-enough-entropy-123456")

    assert password not in password_hash
    assert PasswordHasher().verify(password_hash, password)
    assert digest != b"CF1-test-code-with-enough-entropy-123456"


def test_vault_round_trip_uses_random_nonce() -> None:
    vault = SecretVault(b"v" * 32)
    first = vault.encrypt(b"fake-totp-seed")
    second = vault.encrypt(b"fake-totp-seed")

    assert first != second
    assert vault.decrypt(first) == b"fake-totp-seed"
