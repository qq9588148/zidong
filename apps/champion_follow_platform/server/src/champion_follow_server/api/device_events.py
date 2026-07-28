import base64
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

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
from champion_follow_server.models.admin import GlobalControl
from champion_follow_server.models.ledger import (
    BankrollTelemetry,
    BalanceSnapshot,
    DeviceEventCursor,
    Order,
    Settlement,
)


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
    db_session=Depends(get_session),
):
    cursor = await db_session.get(DeviceEventCursor, context.device.id)
    order = await db_session.scalar(
        select(Order)
        .where(Order.device_id == context.device.id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(1)
    )
    settlement = None if order is None else await db_session.scalar(
        select(Settlement).where(Settlement.order_id == order.id)
    )
    bankroll = await db_session.scalar(
        select(BankrollTelemetry)
        .where(BankrollTelemetry.device_id == context.device.id)
        .order_by(BankrollTelemetry.observed_at.desc(), BankrollTelemetry.id.desc())
        .limit(1)
    )
    balance = await db_session.scalar(
        select(BalanceSnapshot)
        .where(BalanceSnapshot.device_id == context.device.id)
        .order_by(BalanceSnapshot.observed_at.desc(), BalanceSnapshot.id.desc())
        .limit(1)
    )
    control = await db_session.get(GlobalControl, "global-stop")
    return {
        "device_id": str(context.device.id),
        "binding_epoch": context.device.binding_epoch,
        "acknowledged_client_seq": (
            cursor.acknowledged_client_seq if cursor else 0
        ),
        "global_stop_enabled": bool(control and control.enabled),
        "last_order": None if order is None else {
            "client_order_id": str(order.client_order_id),
            "period_id": order.period_id,
            "task_revision": order.task_revision,
            "status": order.status,
            "stake_minor": order.stake_minor,
        },
        "settlement": None if settlement is None else {
            "outcome": settlement.outcome,
            "net_pnl_minor": settlement.net_pnl_minor,
            "settled_at": settlement.settled_at,
        },
        "bankroll": None if bankroll is None else {
            "base_minor": bankroll.base_minor,
            "cap_minor": bankroll.cap_minor,
            "unrecovered_loss_minor": bankroll.unrecovered_loss_minor,
            "next_stake_minor": bankroll.next_stake_minor,
            "cycle_id": str(bankroll.cycle_id),
            "cycle_version": bankroll.cycle_version,
            "frozen_reason": bankroll.frozen_reason,
        },
        "balance": None if balance is None else {
            "availability": balance.availability,
            "balance_minor": balance.balance_minor,
            "observed_at": balance.observed_at,
        },
        "server_time": request.app.state.clock.now(),
    }
