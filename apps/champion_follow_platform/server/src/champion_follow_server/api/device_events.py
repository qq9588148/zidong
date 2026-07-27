import base64
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request

from champion_follow_server.api.dependencies import (
    DeviceContext,
    require_active_device_context,
)
from champion_follow_server.db.session import get_session
from champion_follow_server.schemas.device_events import (
    ClientEventEnvelope,
    canonical_event_bytes,
)
from champion_follow_server.security.device_keys import (
    InvalidDeviceProof,
    verify_bound_device_signature,
)
from champion_follow_server.services.device_ledger import EventConflict, OrderConflict


router = APIRouter()


@router.post("/v1/device/events")
async def ingest_device_event(
    body: ClientEventEnvelope,
    request: Request,
    context: DeviceContext = Depends(require_active_device_context),
    db_session=Depends(get_session),
):
    if body.device_id != context.device.id:
        raise HTTPException(status_code=409, detail="event conflict")
    canonical = canonical_event_bytes(body)
    try:
        signature = base64.b64decode(body.signature, validate=True)
        verify_bound_device_signature(
            public_key_spki_der=context.device.public_key_spki_der,
            proof_der_b64=body.signature,
            message=canonical,
        )
        ack = await request.app.state.device_ledger.ingest_event(
            db_session,
            device=context.device,
            envelope=body,
            signature_der=signature,
            canonical_digest=hashlib.sha256(canonical).digest(),
        )
        await db_session.commit()
    except (InvalidDeviceProof, EventConflict, OrderConflict, ValueError):
        await db_session.rollback()
        raise HTTPException(status_code=409, detail="event conflict") from None
    return {"ack_seq": ack}


@router.get("/v1/device/sync")
async def device_sync(
    request: Request,
    context: DeviceContext = Depends(require_active_device_context),
):
    return {
        "device_id": str(context.device.id),
        "server_time": request.app.state.clock.now(),
    }
