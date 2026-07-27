import pytest
from starlette.websockets import WebSocketDisconnect
from uuid import uuid4


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
