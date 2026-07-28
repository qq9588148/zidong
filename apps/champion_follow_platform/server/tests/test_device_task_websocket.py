import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select
from starlette.websockets import WebSocketDisconnect
from uuid import uuid4

from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AccountStatus,
    Device,
    DeviceStatus,
    SessionKind,
)
from champion_follow_server.models.device_tasks import DeviceTaskRevision


@pytest.mark.asyncio
async def test_reconnect_receives_highest_cancel_not_older_bet(
    ws_client,
    device_access_token,
    revision_context,
    committed_bet_then_cancel,
    auth_session_factory,
    revision_service,
) -> None:
    async with auth_session_factory() as session:
        reloaded = await revision_service.current_head(
            session, revision_context[0].id, "2607270001"
        )
        assert reloaded.revision == 2
    with ws_client.websocket_connect(
        "/ws/v1/device-tasks",
        headers={"Authorization": f"Bearer {device_access_token}"},
    ) as websocket:
        websocket.send_json(
            {
                "type": "SYNC",
                "period_id": "2607270001",
                "known_revision": 1,
            }
        )
        message = websocket.receive_json()
    assert message["type"] == "TASK"
    assert message["task"]["revision"] == 2
    assert message["task"]["action"] == "CANCEL"


@pytest.mark.asyncio
async def test_device_cannot_request_another_devices_task(
    ws_client, device_access_token, revision_context, other_device_task
) -> None:
    with ws_client.websocket_connect(
        "/ws/v1/device-tasks",
        headers={"Authorization": f"Bearer {device_access_token}"},
    ) as websocket:
        websocket.send_json(
            {
                "type": "SYNC",
                "period_id": other_device_task.period_id,
                "known_revision": 0,
            }
        )
        assert websocket.receive_json() == {
            "type": "NO_TASK",
            "period_id": other_device_task.period_id,
            "highest_revision": 0,
        }


@pytest.mark.asyncio
async def test_socket_rejects_missing_auth_and_device_id_in_first_frame(
    ws_client, device_access_token, revision_context
) -> None:
    with pytest.raises(WebSocketDisconnect) as missing:
        with ws_client.websocket_connect("/ws/v1/device-tasks"):
            pass
    assert missing.value.code == 4401

    with ws_client.websocket_connect(
        "/ws/v1/device-tasks",
        headers={"Authorization": f"Bearer {device_access_token}"},
    ) as websocket:
        websocket.send_json(
            {
                "type": "SYNC",
                "period_id": "2607270001",
                "known_revision": 0,
                "device_id": str(revision_context[0].id),
            }
        )
        with pytest.raises(WebSocketDisconnect) as malformed:
            websocket.receive_json()
        assert malformed.value.code == 4400


@pytest.mark.asyncio
async def test_live_revocation_closes_before_next_notification(
    ws_client,
    device_access_token,
    revision_context,
    auth_session_factory,
    test_app,
) -> None:
    device, _threshold = revision_context
    with ws_client.websocket_connect(
        "/ws/v1/device-tasks",
        headers={"Authorization": f"Bearer {device_access_token}"},
    ) as websocket:
        websocket.send_json(
            {
                "type": "SYNC",
                "period_id": "no-current-task",
                "known_revision": 0,
            }
        )
        assert websocket.receive_json()["type"] == "NO_TASK"
        async with auth_session_factory() as session:
            authenticated = await test_app.state.session_service.authenticate_access(
                session, device_access_token
            )
            await test_app.state.session_service.revoke_session(
                session, authenticated.auth_session
            )
            await session.commit()
        websocket.portal.call(
            test_app.state.task_hub.publish,
            device.id,
            uuid4(),
        )
        with pytest.raises(WebSocketDisconnect) as revoked:
            websocket.receive_json()
        assert revoked.value.code == 4401


@pytest.mark.asyncio
async def test_hundred_devices_receive_only_their_own_highest_revision(
    ws_client,
    test_app,
    auth_session_factory,
    clock,
) -> None:
    account_ids = []
    device_tokens = []
    period_id = "hundred-device-isolation"
    try:
        async with auth_session_factory() as session:
            for index in range(100):
                account = Account(
                    username_canonical=f"hundred-{index}-{uuid4().hex}",
                    password_hash="test-hash",
                    role=AccountRole.USER,
                    status=AccountStatus.ACTIVE,
                    admin_slot=None,
                )
                session.add(account)
                await session.flush()
                public_key = f"hundred-device-public-key-{index}".encode()
                device = Device(
                    account_id=account.id,
                    public_key_spki_der=public_key,
                    public_key_fingerprint=hashlib.sha256(public_key).digest(),
                    binding_epoch=1,
                    status=DeviceStatus.ACTIVE,
                )
                session.add(device)
                await session.flush()
                pair = await test_app.state.session_service.issue(
                    session,
                    account=account,
                    kind=SessionKind.USER,
                    device=device,
                )
                account_ids.append(account.id)
                device_tokens.append((device.id, pair.access_token))
            await session.commit()

        async with auth_session_factory() as session:
            for device_id, _token in device_tokens:
                await test_app.state.task_revision_service.publish_cancel(
                    session,
                    device_id=device_id,
                    period_id=period_id,
                    reason="data_gap",
                    expires_at=clock.now() + timedelta(minutes=5),
                )
                await test_app.state.task_revision_service.publish_cancel(
                    session,
                    device_id=device_id,
                    period_id=period_id,
                    reason="global_stop",
                    expires_at=clock.now() + timedelta(minutes=5),
                )
            await session.commit()

        for device_id, access_token in device_tokens:
            with ws_client.websocket_connect(
                "/ws/v1/device-tasks",
                headers={"Authorization": f"Bearer {access_token}"},
            ) as websocket:
                websocket.send_json(
                    {
                        "type": "SYNC",
                        "period_id": period_id,
                        "known_revision": 0,
                    }
                )
                task = websocket.receive_json()["task"]
            assert task["device_id"] == str(device_id)
            assert task["period_id"] == period_id
            assert task["revision"] == 2
            assert task["action"] == "CANCEL"
            assert task["payload"] == {"reason": "global_stop"}

        async with auth_session_factory() as session:
            duplicate_count = await session.scalar(
                select(func.count()).select_from(
                    select(
                        DeviceTaskRevision.device_id,
                        DeviceTaskRevision.period_id,
                        DeviceTaskRevision.revision,
                    )
                    .where(
                        DeviceTaskRevision.device_id.in_(
                            [device_id for device_id, _token in device_tokens]
                        )
                    )
                    .group_by(
                        DeviceTaskRevision.device_id,
                        DeviceTaskRevision.period_id,
                        DeviceTaskRevision.revision,
                    )
                    .having(func.count() > 1)
                    .subquery()
                )
            )
            assert duplicate_count == 0
    finally:
        if account_ids:
            async with auth_session_factory() as cleanup:
                await cleanup.execute(
                    delete(Account).where(Account.id.in_(account_ids))
                )
                await cleanup.commit()
