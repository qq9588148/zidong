import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, text

from champion_follow.contracts.thresholds import PreviewWindow, ThresholdPreviewResult
from champion_follow_server.models.admin import (
    AuditEvent,
    GlobalControl,
    ThresholdConfig,
    ThresholdPreview,
)
from champion_follow_server.models.auth import Account, AuthorizationCode
from champion_follow_server.models.auth import Device, DeviceStatus
from champion_follow_server.models.device_tasks import (
    DeviceTaskHead,
    DeviceTaskRevision,
    TaskAction,
)
from champion_follow_server.schemas.device_tasks import BetPayload
from champion_follow_server.services.thresholds import ThresholdService


@pytest.fixture
async def admin_headers(
    client, auth_session_factory, confirmed_admin
):
    response = await client.post(
        "/api/v1/admin/session",
        json={
            "username": "owner",
            "password": "test-admin-password-with-16-chars",
        },
        headers={"Origin": "https://console.example.test"},
    )
    assert response.status_code == 200
    body = response.json()
    headers = {
        "Authorization": f"Bearer {body['access_token']}",
        "X-CSRF-Token": body["csrf_token"],
        "Origin": "https://console.example.test",
        "X-Request-ID": "admin-api-fixture",
    }
    yield headers
    account, _seed = confirmed_admin
    async with auth_session_factory() as cleanup:
        await cleanup.execute(text("TRUNCATE TABLE audit_events RESTART IDENTITY"))
        await cleanup.execute(delete(GlobalControl))
        await cleanup.execute(
            delete(ThresholdConfig).where(
                ThresholdConfig.created_by_account_id == account.id
            )
        )
        await cleanup.execute(
            delete(ThresholdPreview).where(
                ThresholdPreview.created_by_account_id == account.id
            )
        )
        await cleanup.execute(
            delete(AuthorizationCode).where(
                AuthorizationCode.consumed_at.is_(None)
            )
        )
        await cleanup.commit()


def _bet_payload(threshold_version: int) -> BetPayload:
    return BetPayload(
        signal_id=UUID("00000000-0000-0000-0000-000000000810"),
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
async def test_user_cannot_read_admin_overview(client, device_access_token) -> None:
    response = await client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": f"Bearer {device_access_token}"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_own_report_is_scoped_to_authenticated_user(
    client, device_access_token, revision_context
) -> None:
    device, _threshold = revision_context
    response = await client.get(
        "/api/v1/me/report?account_id=00000000-0000-0000-0000-000000000999",
        headers={"Authorization": f"Bearer {device_access_token}"},
    )

    assert response.status_code == 200
    assert response.json()["account_id"] == str(device.account_id)


@pytest.mark.asyncio
async def test_admin_mutation_requires_trusted_origin_and_csrf(
    client, admin_headers
) -> None:
    authorization_only = {"Authorization": admin_headers["Authorization"]}
    response = await client.post(
        "/api/v1/admin/global-stop",
        json={"enabled": True, "reason": "must not be accepted"},
        headers=authorization_only,
    )
    assert response.status_code == 403

    response = await client.post(
        "/api/v1/admin/global-stop",
        json={"enabled": True, "reason": "must not be accepted"},
        headers={
            **authorization_only,
            "Origin": "https://console.example.test",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_global_stop_cancels_every_live_bet(
    client,
    admin_headers,
    auth_session_factory,
    revision_context,
    revision_service,
    clock,
) -> None:
    device, threshold = revision_context
    async with auth_session_factory() as session:
        for period_id in ("2607270801", "2607270802"):
            await revision_service.publish_bet(
                session,
                device_id=device.id,
                period_id=period_id,
                payload=_bet_payload(threshold.config_version),
                expires_at=clock.now().replace(hour=5),
            )
        await session.commit()

    response = await client.post(
        "/api/v1/admin/global-stop",
        json={"enabled": True, "reason": "operator safety stop"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    async with auth_session_factory() as session:
        heads = (
            await session.scalars(
                select(DeviceTaskRevision)
                .join(DeviceTaskHead, DeviceTaskHead.task_id == DeviceTaskRevision.id)
                .where(DeviceTaskHead.device_id == device.id)
            )
        ).all()
        control = await session.get(GlobalControl, "global-stop")
        audit = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "GLOBAL_STOP_UPDATED")
            .order_by(AuditEvent.id.desc())
        )
    assert len(heads) == 2
    assert all(head.action == TaskAction.CANCEL for head in heads)
    assert control is not None and control.enabled is True
    assert audit is not None and audit.reason == "operator safety stop"


@pytest.mark.asyncio
async def test_admin_unbind_revokes_device_session_and_cancels_live_task(
    client,
    admin_headers,
    device_access_token,
    auth_session_factory,
    revision_context,
    revision_service,
    clock,
) -> None:
    device, threshold = revision_context
    async with auth_session_factory() as session:
        await revision_service.publish_bet(
            session,
            device_id=device.id,
            period_id="2607270810",
            payload=_bet_payload(threshold.config_version),
            expires_at=clock.now().replace(hour=5),
        )
        await session.commit()

    response = await client.post(
        f"/api/v1/admin/devices/{device.id}/unbind",
        json={"reason": "replace damaged windows device"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["cancelled_task_count"] == 1
    old_session = await client.get(
        "/api/v1/me/report",
        headers={"Authorization": f"Bearer {device_access_token}"},
    )
    assert old_session.status_code == 401
    async with auth_session_factory() as session:
        stored = await session.get(Device, device.id)
        head = await session.scalar(
            select(DeviceTaskRevision)
            .join(DeviceTaskHead, DeviceTaskHead.task_id == DeviceTaskRevision.id)
            .where(
                DeviceTaskHead.device_id == device.id,
                DeviceTaskHead.period_id == "2607270810",
            )
        )
    assert stored is not None and stored.status == DeviceStatus.UNBOUND
    assert head is not None and head.action == TaskAction.CANCEL


@pytest.mark.asyncio
async def test_authorization_code_plaintext_is_returned_once_and_not_audited(
    client, admin_headers, auth_session_factory
) -> None:
    response = await client.post(
        "/api/v1/admin/authorization-codes",
        json={
            "purpose": "REGISTER",
            "target_account_id": None,
            "reason": "license one test user",
        },
        headers=admin_headers,
    )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    plaintext = response.json()["authorization_code"]
    assert plaintext.startswith("CF1-")
    users = await client.get("/api/v1/admin/users", headers=admin_headers)
    assert users.status_code == 200
    assert plaintext not in users.text
    async with auth_session_factory() as session:
        audit = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "AUTH_CODE_CREATED")
            .order_by(AuditEvent.id.desc())
        )
    serialized = json.dumps(
        {"old": audit.old_state, "new": audit.new_state}, sort_keys=True
    )
    assert plaintext not in serialized
    assert "authorization_code" not in serialized


class _AdminPreviewSource:
    async def preview(self, **_kwargs):
        return ThresholdPreviewResult(
            preview_id=uuid4(),
            watermark_snapshot_id=uuid4(),
            generated_at=_kwargs["as_of"],
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


@pytest.mark.asyncio
async def test_threshold_activation_requires_matching_preview(
    client, admin_headers, test_app
) -> None:
    test_app.state.threshold_service = ThresholdService(
        _AdminPreviewSource(),
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
    missing = await client.post(
        "/api/v1/admin/thresholds",
        json={
            **proposal,
            "preview_id": str(uuid4()),
            "reason": "activate tested threshold",
        },
        headers=admin_headers,
    )
    assert missing.status_code == 409

    preview = await client.post(
        "/api/v1/admin/thresholds/preview",
        json=proposal,
        headers=admin_headers,
    )
    assert preview.status_code == 200
    activated = await client.post(
        "/api/v1/admin/thresholds",
        json={
            **proposal,
            "preview_id": preview.json()["preview_id"],
            "reason": "activate tested threshold",
        },
        headers=admin_headers,
    )
    assert activated.status_code == 200
    assert activated.json()["scope"] == "GLOBAL"
