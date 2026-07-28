import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response

from champion_follow.contracts.events import (
    CollectorBatch,
    CollectorHeartbeat,
    CollectorSessionRequest,
    CollectorSessionResponse,
    CollectorWireAck,
    CollectorWireBatch,
    EventKind,
    NormalizedEvent,
)
from champion_follow.repositories.ingestion import (
    CollectorContractError,
    EventConflict,
    SequenceGap,
)


router = APIRouter(prefix="/v1/collector", tags=["collector"])
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _safe_error(status_code: int, code: str, **detail):
    raise HTTPException(status_code, detail={"code": code, **detail})


async def _authenticated_identity(request: Request, claimed_wire_id: str):
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        _safe_error(401, "collector_auth_rejected")
    parts = values[0].split(" ")
    if len(parts) != 2 or parts[0] != "Bearer" or not parts[1]:
        _safe_error(401, "collector_auth_rejected")

    credential_digest = hashlib.sha256(parts[1].encode("utf-8")).hexdigest()
    identity = await request.app.state.ingestion.repository.authenticate(
        credential_digest
    )
    if identity is None:
        _safe_error(401, "collector_auth_rejected")
    if identity.wire_id != claimed_wire_id:
        _safe_error(403, "collector_identity_mismatch")
    return identity


def _normalized_event(record) -> NormalizedEvent:
    event = record.event
    kind = {
        "BET": EventKind.BET,
        "CANCEL": EventKind.CANCEL,
        "CANCEL_UNATTRIBUTED": EventKind.UNATTRIBUTED_CANCEL,
        "CLOSE": EventKind.CLOSE,
        "RESULT": EventKind.RESULT,
        "CAPTURE_GAP": EventKind.CAPTURE_GAP,
        "ISSUE_STATUS": EventKind.ISSUE_STATUS,
    }[event.kind]
    money = event.kind in {"BET", "CANCEL"}
    return NormalizedEvent(
        event_key=event.event_key,
        local_sequence=record.seq,
        actor_key=event.actor_key if money else None,
        issue=event.issue,
        kind=kind,
        source_ms=event.source_ms,
        received_at=EPOCH + timedelta(milliseconds=event.received_at_ms),
        play=event.play if money else None,
        amount_fen=int(event.amount_minor) if money else None,
        result_digits=event.digits if event.kind == "RESULT" else None,
        parser_version=event.parser_version,
        gap_reason=event.reason if event.kind == "CAPTURE_GAP" else None,
        reported_complete=event.complete
        if event.kind == "ISSUE_STATUS"
        else None,
        reported_reasons=event.reasons if event.kind == "ISSUE_STATUS" else None,
    )


def _internal_batch(identity, batch: CollectorWireBatch) -> CollectorBatch:
    events = tuple(_normalized_event(record) for record in batch.records)
    return CollectorBatch(
        collector_id=identity.collector_id,
        namespace_version=batch.namespace_version,
        sequence_start=batch.from_seq,
        sequence_end=batch.to_seq,
        issue_hint=events[0].issue,
        events=events,
        wire_digests=tuple(record.digest for record in batch.records),
    )


def _raise_public_ingestion_error(error):
    if isinstance(error, SequenceGap):
        _safe_error(
            409,
            "sequence_gap",
            ack_seq=error.highest_contiguous_sequence,
        )
    if isinstance(error, EventConflict):
        _safe_error(409, "collector_sequence_conflict")
    if isinstance(error, CollectorContractError):
        code = str(error)
        if code == "partial_sequence_overlap":
            _safe_error(409, "collector_sequence_conflict")
        if code in {"namespace_version_mismatch", "parser_version_mismatch"}:
            _safe_error(409, code)
        _safe_error(403, "collector_identity_mismatch")


@router.post("/session", response_model=CollectorSessionResponse)
async def collector_session(
    body: CollectorSessionRequest, request: Request
) -> CollectorSessionResponse:
    identity = await _authenticated_identity(request, body.collector_id)
    try:
        session = await request.app.state.ingestion.repository.collector_session(
            identity.collector_id,
            body.namespace_version,
        )
    except CollectorContractError as error:
        _raise_public_ingestion_error(error)
        raise AssertionError("unreachable")
    return CollectorSessionResponse(
        ack_seq=session.ack_sequence,
        ack_event_key=session.ack_event_key,
        history_anchor_event_key=session.history_anchor_event_key,
        namespace_empty=session.namespace_empty,
    )


@router.post("/events", response_model=CollectorWireAck)
async def collector_events(
    body: CollectorWireBatch, request: Request
) -> CollectorWireAck:
    identity = await _authenticated_identity(request, body.collector_id)
    batch = _internal_batch(identity, body)
    try:
        ack = await request.app.state.ingestion.accept(batch)
    except (SequenceGap, EventConflict, CollectorContractError) as error:
        _raise_public_ingestion_error(error)
        raise AssertionError("unreachable")
    coordinator = getattr(request.app.state, "processing_coordinator", None)
    if coordinator is not None:
        await coordinator.process(
            namespace_id=identity.namespace_id,
            namespace_version=identity.namespace_version,
        )
    return CollectorWireAck(ack_seq=ack.highest_contiguous_sequence)


@router.post("/heartbeat", status_code=204, response_class=Response)
async def collector_heartbeat(
    body: CollectorHeartbeat, request: Request
) -> Response:
    identity = await _authenticated_identity(request, body.collector_id)
    await request.app.state.ingestion.repository.record_heartbeat(
        identity.collector_id,
        issue=body.issue,
        phase=body.phase,
        countdown_ms=body.countdown_ms,
        observed_at_ms=body.observed_at_ms,
        last_journal_sequence=body.last_journal_seq,
        capture_healthy=body.capture_healthy,
    )
    coordinator = getattr(request.app.state, "processing_coordinator", None)
    if coordinator is not None:
        await coordinator.process(
            namespace_id=identity.namespace_id,
            namespace_version=identity.namespace_version,
        )
    return Response(status_code=204)
