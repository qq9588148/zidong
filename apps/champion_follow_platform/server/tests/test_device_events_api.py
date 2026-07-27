import base64
import json
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from jsonschema import Draft202012Validator, FormatChecker

from champion_follow_server.schemas.device_events import (
    ClientEventEnvelope,
    canonical_event_bytes,
)


def sign_event(fake_device_keypair, value: dict) -> dict:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return {
        **value,
        "signature": base64.b64encode(
            fake_device_keypair.private_key.sign(canonical)
        ).decode("ascii"),
    }


@pytest.mark.asyncio
async def test_signed_device_event_is_acked_once_and_conflict_is_generic(
    client,
    device_access_token,
    revision_context,
    fake_device_keypair,
    clock,
) -> None:
    device, _threshold = revision_context
    value = {
        "schema_version": "client-event-v1",
        "device_id": str(device.id),
        "binding_epoch": device.binding_epoch,
        "client_seq": 1,
        "event_id": str(uuid4()),
        "observed_at": clock.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "type": "TASK_RECEIVED",
        "payload": {
            "task_id": "00000000-0000-0000-0000-000000000010",
            "period_id": "2607270001",
            "revision": 1,
        },
    }
    event = sign_event(fake_device_keypair, value)
    headers = {"Authorization": f"Bearer {device_access_token}"}

    first = await client.post("/v1/device/events", json=event, headers=headers)
    assert first.status_code == 200
    assert first.json() == {"ack_seq": 1}
    replay = await client.post("/v1/device/events", json=event, headers=headers)
    assert replay.status_code == 200
    assert replay.json() == {"ack_seq": 1}

    changed = sign_event(
        fake_device_keypair,
        {**value, "payload": {**value["payload"], "revision": 2}},
    )
    conflict = await client.post(
        "/v1/device/events", json=changed, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "event conflict"}


def test_all_client_event_fixtures_validate_and_verify() -> None:
    root = Path(__file__).parents[2]
    contract = json.loads(
        (root / "contracts" / "client-event-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = Draft202012Validator(
        contract, format_checker=FormatChecker()
    )
    public_key = ec.derive_private_key(7, ec.SECP256R1()).public_key()
    paths = sorted((root / "contracts" / "fixtures").glob("client-event-*-v1.json"))
    assert len(paths) == 9
    for path in paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        validator.validate(value)
        event = ClientEventEnvelope.model_validate(value)
        public_key.verify(
            base64.b64decode(event.signature, validate=True),
            canonical_event_bytes(event),
            ec.ECDSA(hashes.SHA256()),
        )
