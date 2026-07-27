import asyncio
import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator, FormatChecker

from champion_follow_server.schemas.device_tasks import (
    BetPayload,
    CancelReason,
    SignedTaskEnvelope,
)
from champion_follow_server.security.task_signing import canonical_task_bytes
from champion_follow_server.services.task_hub import TaskHub


def bet_payload(threshold_version: int) -> BetPayload:
    return BetPayload(
        signal_id="00000000-0000-0000-0000-000000000010",
        signal_version=3,
        actor_ref="A000007",
        ball=2,
        direction="ODD",
        threshold_version=threshold_version,
        odds_micros=1_960_000,
        user_level="CORE",
        sample_count=618,
        conservative_win_rate="0.5431000000",
        conservative_unit_return="0.0645000000",
        followable_rate="0.8120000000",
    )


@pytest.mark.asyncio
async def test_cancel_tombstone_remains_head_after_late_old_bet(
    auth_session_factory, revision_context, revision_service, clock
) -> None:
    device, threshold = revision_context
    future_expiry = clock.now() + timedelta(minutes=10)
    async with auth_session_factory() as session:
        bet = await revision_service.publish_bet(
            session,
            device_id=device.id,
            period_id="2607270001",
            payload=bet_payload(threshold.config_version),
            expires_at=future_expiry,
        )
        cancel = await revision_service.publish_cancel(
            session,
            device_id=device.id,
            period_id="2607270001",
            reason="champion_withdrew",
            expires_at=future_expiry,
        )
        assert (bet.revision, cancel.revision, cancel.action.value) == (
            1,
            2,
            "CANCEL",
        )
        head = await revision_service.current_head(
            session, device.id, "2607270001"
        )
        assert head.revision == 2
        duplicate = await revision_service.publish_cancel(
            session,
            device_id=device.id,
            period_id="2607270001",
            reason="champion_withdrew",
            expires_at=future_expiry,
        )
        assert duplicate.id == cancel.id
        await session.commit()


@pytest.mark.asyncio
async def test_two_publishers_receive_distinct_monotonic_revisions(
    auth_session_factory, revision_context, revision_service_factory, clock
) -> None:
    device, _threshold = revision_context
    future_expiry = clock.now() + timedelta(minutes=10)

    async def publish(reason):
        async with auth_session_factory() as session:
            row = await revision_service_factory().publish_cancel(
                session,
                device_id=device.id,
                period_id="2607270002",
                reason=reason,
                expires_at=future_expiry,
            )
            await session.commit()
            return row.revision

    results = await asyncio.gather(
        publish("data_gap"), publish("global_stop")
    )
    assert sorted(results) == [1, 2]


def test_cross_platform_contract_fixtures_validate_and_verify() -> None:
    root = Path(__file__).parents[2]
    contract = json.loads(
        (root / "contracts" / "device-task-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = Draft202012Validator(
        contract, format_checker=FormatChecker()
    )
    public_key = Ed25519PrivateKey.from_private_bytes(
        bytes(range(32))
    ).public_key()
    fixtures = []
    for name in ("device-task-bet-v1.json", "device-task-cancel-v1.json"):
        value = json.loads(
            (root / "contracts" / "fixtures" / name).read_text(
                encoding="utf-8"
            )
        )
        validator.validate(value)
        SignedTaskEnvelope.model_validate(value)
        signature = base64.urlsafe_b64decode(value["signature"])
        unsigned = {key: child for key, child in value.items() if key != "signature"}
        public_key.verify(signature, canonical_task_bytes(unsigned))
        fixtures.append(value)

    mismatch = {**fixtures[0], "action": "CANCEL"}
    assert list(validator.iter_errors(mismatch))


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", list(CancelReason))
async def test_every_invalidation_reason_creates_a_higher_signed_tombstone(
    reason,
    auth_session_factory,
    revision_context,
    revision_service,
    clock,
) -> None:
    device, threshold = revision_context
    future_expiry = clock.now() + timedelta(minutes=10)
    async with auth_session_factory() as session:
        bet = await revision_service.publish_bet(
            session,
            device_id=device.id,
            period_id=f"invalidate-{reason.value}",
            payload=bet_payload(threshold.config_version),
            expires_at=future_expiry,
        )
        cancelled = await revision_service.cancel_live_bets(
            session,
            reason=reason,
            device_ids={device.id},
            expires_at=future_expiry,
        )
        assert len(cancelled) == 1
        assert cancelled[0].revision > bet.revision
        revision_service.signed_envelope(cancelled[0])
        await session.commit()


@pytest.mark.asyncio
async def test_task_hub_replaces_stale_pending_notification() -> None:
    from uuid import UUID

    device_id = UUID("00000000-0000-0000-0000-000000000001")
    first = UUID("00000000-0000-0000-0000-000000000010")
    newest = UUID("00000000-0000-0000-0000-000000000011")
    hub = TaskHub()
    queue = hub.connect(device_id)

    hub.publish(device_id, first)
    hub.publish(device_id, newest)
    assert await queue.get() == newest
    hub.disconnect(device_id, queue)
