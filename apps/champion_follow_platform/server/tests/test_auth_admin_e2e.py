import base64
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pyotp
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from champion_follow.contracts.thresholds import PreviewWindow, ThresholdPreviewResult
from champion_follow_server.models.auth import Account, AccountRole
from champion_follow_server.schemas.device_tasks import BetPayload
from champion_follow_server.security.device_keys import (
    device_login_message,
    enrollment_message,
)
from champion_follow_server.services.admin_bootstrap import (
    AdminAlreadyExists,
    AdminBootstrapService,
)
from champion_follow_server.services.thresholds import ThresholdService


ADMIN_ORIGIN = "https://console.example.test"
ADMIN_PASSWORD = "test-e2e-admin-password"
USER_PASSWORD = "test-e2e-user-password"
CURRENT_PERIOD = "2607270001"
PREVIOUS_PERIOD = "2607269999"


@pytest_asyncio.fixture
async def empty_auth_stack(auth_session_factory):
    async def truncate() -> None:
        async with auth_session_factory() as session:
            await session.execute(
                text("TRUNCATE TABLE app_accounts RESTART IDENTITY CASCADE")
            )
            await session.commit()

    await truncate()
    try:
        yield
    finally:
        await truncate()


class _EndToEndPreviewSource:
    async def preview(self, **kwargs):
        return ThresholdPreviewResult(
            preview_id=uuid4(),
            watermark_snapshot_id=uuid4(),
            generated_at=kwargs["as_of"],
            windows=(
                PreviewWindow(
                    days=7,
                    frozen_signal_count=10,
                    executable_signal_count=7,
                    win_count=5,
                    loss_count=2,
                    unit_profit_micros=2_800_000,
                    raw_win_rate=Decimal("0.714285714286"),
                    conservative_win_rate=Decimal("0.358934451832"),
                ),
                PreviewWindow(
                    days=30,
                    frozen_signal_count=40,
                    executable_signal_count=30,
                    win_count=20,
                    loss_count=10,
                    unit_profit_micros=9_200_000,
                    raw_win_rate=Decimal("0.666666666667"),
                    conservative_win_rate=Decimal("0.487954991637"),
                ),
            ),
        )


def _wire_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _signed_event(
    keypair,
    *,
    device_id: UUID,
    binding_epoch: int,
    client_seq: int,
    observed_at: datetime,
    event_type: str,
    payload: dict,
) -> dict:
    unsigned = {
        "schema_version": "client-event-v1",
        "device_id": str(device_id),
        "binding_epoch": binding_epoch,
        "client_seq": client_seq,
        "event_id": str(uuid4()),
        "observed_at": _wire_time(observed_at),
        "type": event_type,
        "payload": payload,
    }
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return {
        **unsigned,
        "signature": base64.b64encode(
            keypair.private_key.sign(canonical)
        ).decode("ascii"),
    }


def _bet_payload(threshold_version: int, signal_id: str) -> BetPayload:
    return BetPayload(
        signal_id=UUID(signal_id),
        signal_version=1,
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
async def test_complete_auth_admin_task_stop_and_reporting_flow(
    empty_auth_stack,
    client,
    ws_client,
    test_app,
    auth_session_factory,
    fake_device_keypair,
    clock,
) -> None:
    bootstrap = AdminBootstrapService(
        test_app.state.password_hasher, test_app.state.secret_vault
    )
    async with auth_session_factory() as session:
        pending = await bootstrap.create_pending_admin(
            session,
            username="e2e-owner",
            password=ADMIN_PASSWORD,
            issuer="Champion Follow Test",
        )
        seed = parse_qs(urlparse(pending.provisioning_uri).query)["secret"][0]
        await bootstrap.confirm_totp(
            session,
            pending.account_id,
            pyotp.TOTP(seed).at(clock.now()),
            now=clock.now(),
        )
        await session.commit()

    async with auth_session_factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(Account).where(
                Account.role == AccountRole.ADMIN,
                Account.admin_slot == 1,
            )
        ) == 1
        with pytest.raises(AdminAlreadyExists):
            await bootstrap.create_pending_admin(
                session,
                username="second-owner",
                password=ADMIN_PASSWORD,
                issuer="Champion Follow Test",
            )
        await session.rollback()

    admin_login = await client.post(
        "/api/v1/admin/session",
        json={
            "username": "e2e-owner",
            "password": ADMIN_PASSWORD,
            "totp": pyotp.TOTP(seed).at(clock.now()),
        },
        headers={"Origin": ADMIN_ORIGIN},
    )
    assert admin_login.status_code == 200
    admin_session = admin_login.json()
    admin_headers = {
        "Authorization": f"Bearer {admin_session['access_token']}",
        "X-CSRF-Token": admin_session["csrf_token"],
        "Origin": ADMIN_ORIGIN,
        "X-Request-ID": "e2e-admin-mutation",
    }

    code_response = await client.post(
        "/api/v1/admin/authorization-codes",
        json={
            "purpose": "REGISTER",
            "target_account_id": None,
            "reason": "license end-to-end test device",
        },
        headers=admin_headers,
    )
    assert code_response.status_code == 201
    authorization_code = code_response.json()["authorization_code"]

    enrollment = await client.post(
        "/api/v1/enrollment/challenge",
        json={"authorization_code": authorization_code},
    )
    assert enrollment.status_code == 200
    enrollment_body = enrollment.json()
    enrollment_id = UUID(enrollment_body["challenge_id"])
    enrollment_nonce = base64.b64decode(enrollment_body["nonce"], validate=True)
    enrollment_proof = base64.b64encode(
        fake_device_keypair.private_key.sign(
            enrollment_message(enrollment_id, enrollment_nonce)
        )
    ).decode("ascii")
    registration = await client.post(
        "/api/v1/enrollment/register",
        json={
            "authorization_code": authorization_code,
            "challenge_id": str(enrollment_id),
            "username": "e2e-user",
            "password": USER_PASSWORD,
            "public_key_spki_der": fake_device_keypair.public_key_spki_der_b64,
            "proof_der": enrollment_proof,
        },
    )
    assert registration.status_code == 200
    registered = registration.json()
    account_id = UUID(registered["account_id"])
    device_id = UUID(registered["device_id"])

    login_challenge = await client.post(
        "/api/v1/auth/device/challenge", json={"username": "e2e-user"}
    )
    assert login_challenge.status_code == 200
    challenge_body = login_challenge.json()
    challenge_id = UUID(challenge_body["challenge_id"])
    login_proof = base64.b64encode(
        fake_device_keypair.private_key.sign(
            device_login_message(
                challenge_id,
                base64.b64decode(challenge_body["nonce"], validate=True),
            )
        )
    ).decode("ascii")
    user_login = await client.post(
        "/api/v1/auth/device/login",
        json={
            "challenge_id": str(challenge_id),
            "username": "e2e-user",
            "password": USER_PASSWORD,
            "proof_der": login_proof,
        },
    )
    assert user_login.status_code == 200
    user_access_token = user_login.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {user_access_token}"}

    test_app.state.threshold_service = ThresholdService(
        _EndToEndPreviewSource(),
        test_app.state.audit_writer,
        test_app.state.clock,
    )
    proposal = {
        "device_id": None,
        "minimum_level": "FORMAL",
        "minimum_conservative_win_rate": "0.5200000000",
        "minimum_conservative_roi": "0.0192000000",
        "minimum_followable_rate": "0.8000000000",
    }
    preview = await client.post(
        "/api/v1/admin/thresholds/preview",
        json=proposal,
        headers=admin_headers,
    )
    assert preview.status_code == 200
    activation = await client.post(
        "/api/v1/admin/thresholds",
        json={
            **proposal,
            "preview_id": preview.json()["preview_id"],
            "reason": "activate reviewed end-to-end threshold",
        },
        headers=admin_headers,
    )
    assert activation.status_code == 200
    threshold_version = activation.json()["config_version"]

    async with auth_session_factory() as session:
        current_bet = await test_app.state.task_revision_service.publish_bet(
            session,
            device_id=device_id,
            period_id=CURRENT_PERIOD,
            payload=_bet_payload(
                threshold_version, "00000000-0000-0000-0000-000000000811"
            ),
            expires_at=clock.now() + timedelta(minutes=10),
        )
        previous_bet = await test_app.state.task_revision_service.publish_bet(
            session,
            device_id=device_id,
            period_id=PREVIOUS_PERIOD,
            payload=_bet_payload(
                threshold_version, "00000000-0000-0000-0000-000000000812"
            ),
            expires_at=clock.now() + timedelta(minutes=10),
        )
        await session.commit()
    signed_bet = test_app.state.task_revision_service.signed_envelope(current_bet)
    assert signed_bet.revision == 1
    assert signed_bet.action == "BET"
    assert signed_bet.payload.actor_ref == "A000007"

    with ws_client.websocket_connect(
        "/ws/v1/device-tasks", headers=user_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "SYNC",
                "period_id": CURRENT_PERIOD,
                "known_revision": 0,
            }
        )
        first_task = websocket.receive_json()["task"]
    assert first_task["revision"] == 1
    assert first_task["action"] == "BET"

    global_stop = await client.post(
        "/api/v1/admin/global-stop",
        json={"enabled": True, "reason": "end-to-end safety stop"},
        headers=admin_headers,
    )
    assert global_stop.status_code == 200
    assert global_stop.json()["enabled"] is True

    with ws_client.websocket_connect(
        "/ws/v1/device-tasks", headers=user_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "SYNC",
                "period_id": CURRENT_PERIOD,
                "known_revision": 1,
            }
        )
        cancel_task = websocket.receive_json()["task"]
    assert cancel_task["revision"] == 2
    assert cancel_task["action"] == "CANCEL"
    assert cancel_task["payload"] == {"reason": "global_stop"}

    with ws_client.websocket_connect(
        "/ws/v1/device-tasks", headers=user_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "SYNC",
                "period_id": CURRENT_PERIOD,
                "known_revision": 0,
            }
        )
        reconnected = websocket.receive_json()["task"]
    assert reconnected["revision"] == 2
    assert reconnected["action"] == "CANCEL"

    before_boundary = datetime(2026, 7, 26, 15, 59, tzinfo=UTC)
    after_boundary = datetime(2026, 7, 26, 16, 1, tzinfo=UTC)
    previous_order_id = uuid4()
    current_order_id = uuid4()
    generation = uuid4()
    events = (
        _signed_event(
            fake_device_keypair,
            device_id=device_id,
            binding_epoch=1,
            client_seq=1,
            observed_at=before_boundary,
            event_type="ORDER_CONFIRMED",
            payload={
                "task_id": str(previous_bet.id),
                "period_id": PREVIOUS_PERIOD,
                "task_revision": 1,
                "generation": str(generation),
                "client_order_id": str(previous_order_id),
                "platform_order_ref": "sha256:" + "a" * 64,
                "stake_minor": 100,
                "confirmed_at": _wire_time(before_boundary),
            },
        ),
        _signed_event(
            fake_device_keypair,
            device_id=device_id,
            binding_epoch=1,
            client_seq=2,
            observed_at=before_boundary + timedelta(seconds=30),
            event_type="SETTLEMENT_CONFIRMED",
            payload={
                "client_order_id": str(previous_order_id),
                "period_id": PREVIOUS_PERIOD,
                "outcome": "LOSS",
                "net_pnl_minor": -100,
                "settled_at": _wire_time(before_boundary + timedelta(seconds=30)),
            },
        ),
        _signed_event(
            fake_device_keypair,
            device_id=device_id,
            binding_epoch=1,
            client_seq=3,
            observed_at=after_boundary,
            event_type="ORDER_CONFIRMED",
            payload={
                "task_id": str(current_bet.id),
                "period_id": CURRENT_PERIOD,
                "task_revision": 1,
                "generation": str(generation),
                "client_order_id": str(current_order_id),
                "platform_order_ref": "sha256:" + "b" * 64,
                "stake_minor": 100,
                "confirmed_at": _wire_time(after_boundary),
            },
        ),
        _signed_event(
            fake_device_keypair,
            device_id=device_id,
            binding_epoch=1,
            client_seq=4,
            observed_at=after_boundary + timedelta(seconds=30),
            event_type="SETTLEMENT_CONFIRMED",
            payload={
                "client_order_id": str(current_order_id),
                "period_id": CURRENT_PERIOD,
                "outcome": "WIN",
                "net_pnl_minor": 96,
                "settled_at": _wire_time(after_boundary + timedelta(seconds=30)),
            },
        ),
        _signed_event(
            fake_device_keypair,
            device_id=device_id,
            binding_epoch=1,
            client_seq=5,
            observed_at=clock.now() - timedelta(minutes=1),
            event_type="BALANCE_SNAPSHOT",
            payload={"availability": "AVAILABLE", "balance_minor": 9_996},
        ),
    )
    for expected_ack, event in enumerate(events, start=1):
        response = await client.post(
            "/v1/device/events", json=event, headers=user_headers
        )
        assert response.status_code == 200
        assert response.json() == {"ack_seq": expected_ack}

    own_report_response = await client.get(
        "/api/v1/me/report", headers=user_headers
    )
    assert own_report_response.status_code == 200
    own_report = own_report_response.json()
    admin_report_response = await client.get(
        f"/api/v1/admin/users/{account_id}/report", headers=admin_headers
    )
    assert admin_report_response.status_code == 200
    admin_report = admin_report_response.json()
    assert admin_report == own_report
    assert own_report["current_balance_minor"] == 9_996
    assert own_report["periods"]["today"] == {
        "turnover_minor": 100,
        "net_pnl_minor": 96,
        "settled_bet_count": 1,
    }
    assert own_report["periods"]["yesterday"]["net_pnl_minor"] == -100
    assert own_report["periods"]["week"]["net_pnl_minor"] == 96
    assert own_report["periods"]["cumulative"]["net_pnl_minor"] == -4

    overview = await client.get("/api/v1/admin/overview", headers=admin_headers)
    assert overview.status_code == 200
    assert overview.json()["periods"]["cumulative"]["net_pnl_minor"] == -4
    denied = await client.get("/api/v1/admin/overview", headers=user_headers)
    assert denied.status_code == 403

    audit_response = await client.get("/api/v1/admin/audit", headers=admin_headers)
    assert audit_response.status_code == 200
    audit_body = audit_response.json()
    actions = Counter(item["action"] for item in audit_body["items"])
    assert actions == Counter(
        {
            "AUTH_CODE_CREATED": 1,
            "ACCOUNT_REGISTERED": 1,
            "DEVICE_BOUND": 1,
            "THRESHOLD_ACTIVATED": 1,
            "GLOBAL_STOP_UPDATED": 1,
        }
    )
    assert authorization_code not in audit_response.text
