import json
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical_task_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


class TaskSigner:
    def __init__(
        self, private_key: Ed25519PrivateKey, key_version: str
    ) -> None:
        self._private_key = private_key
        self.public_key = private_key.public_key()
        self.key_version = key_version

    def sign(self, envelope: Mapping[str, Any]) -> bytes:
        if envelope.get("signing_key_version") != self.key_version:
            raise ValueError("signing key version mismatch")
        return self._private_key.sign(canonical_task_bytes(envelope))


def load_task_signer(path: Path, key_version: str) -> TaskSigner:
    try:
        pem_bytes = path.read_bytes()
        private_key = serialization.load_pem_private_key(
            pem_bytes, password=None
        )
    except (OSError, TypeError, ValueError, UnsupportedAlgorithm):
        raise ValueError("invalid task signing key") from None
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("invalid task signing key")
    return TaskSigner(private_key, key_version)
