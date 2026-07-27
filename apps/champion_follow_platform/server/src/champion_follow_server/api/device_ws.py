import asyncio
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from champion_follow_server.models.device_tasks import DeviceTaskRevision
from champion_follow_server.schemas.device_tasks import utc_rfc3339


router = APIRouter()


class SyncFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^SYNC$")
    period_id: str = Field(min_length=1, max_length=64)
    known_revision: int = Field(ge=0)


async def _active_device(websocket: WebSocket, token: str):
    async with websocket.app.state.auth_sessions() as session:
        authenticated = (
            await websocket.app.state.session_service.authenticate_access(
                session, token
            )
        )
        if authenticated is None:
            return None
        return await websocket.app.state.session_service.active_device_for(
            session, authenticated.auth_session
        )


async def _send_authoritative(
    websocket: WebSocket,
    *,
    device_id: UUID,
    period_id: str,
    known_revision: int,
) -> int:
    async with websocket.app.state.auth_sessions() as session:
        head = await websocket.app.state.task_revision_service.current_head(
            session, device_id, period_id
        )
        if head is None:
            await websocket.send_json(
                {
                    "type": "NO_TASK",
                    "period_id": period_id,
                    "highest_revision": 0,
                }
            )
            return 0
        if head.revision <= known_revision:
            await websocket.send_json(
                {
                    "type": "UP_TO_DATE",
                    "period_id": period_id,
                    "highest_revision": head.revision,
                }
            )
            return head.revision
        await websocket.send_json(
            {
                "type": "TASK",
                "task": websocket.app.state.task_revision_service.wire_envelope(
                    head
                ),
            }
        )
        return head.revision


@router.websocket("/ws/v1/device-tasks")
async def device_tasks(websocket: WebSocket) -> None:
    authorization = websocket.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        await websocket.close(code=4401)
        return
    device = await _active_device(websocket, token)
    if device is None:
        await websocket.close(code=4401)
        return
    queue = websocket.app.state.task_hub.connect(device.id)
    await websocket.accept()
    last_sent: dict[str, int] = {}
    try:
        try:
            raw = await websocket.receive_json()
            sync = SyncFrame.model_validate(raw)
        except (ValidationError, ValueError, TypeError):
            await websocket.close(code=4400)
            return
        last_sent[sync.period_id] = await _send_authoritative(
            websocket,
            device_id=device.id,
            period_id=sync.period_id,
            known_revision=sync.known_revision,
        )
        while True:
            try:
                task_id = await asyncio.wait_for(queue.get(), timeout=10)
            except TimeoutError:
                if await _active_device(websocket, token) is None:
                    await websocket.close(code=4401)
                    return
                await websocket.send_json(
                    {
                        "type": "HEARTBEAT",
                        "server_time": utc_rfc3339(
                            websocket.app.state.clock.now()
                        ),
                    }
                )
                continue
            try:
                if await _active_device(websocket, token) is None:
                    await websocket.close(code=4401)
                    return
                async with websocket.app.state.auth_sessions() as session:
                    notified = await session.get(DeviceTaskRevision, task_id)
                    if notified is None or notified.device_id != device.id:
                        continue
                    period_id = notified.period_id
                    head = await websocket.app.state.task_revision_service.current_head(
                        session, device.id, period_id
                    )
                    if head is None or head.revision <= last_sent.get(period_id, 0):
                        continue
                    await websocket.send_json(
                        {
                            "type": "TASK",
                            "task": websocket.app.state.task_revision_service.wire_envelope(
                                head
                            ),
                        }
                    )
                    last_sent[period_id] = head.revision
            finally:
                queue.task_done()
    except WebSocketDisconnect:
        return
    finally:
        websocket.app.state.task_hub.disconnect(device.id, queue)
