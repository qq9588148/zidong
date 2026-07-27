import hashlib
import hmac
import json
import re
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from champion_follow.domain.markets import parse_play


DIGEST = re.compile(r"^[0-9a-f]{64}$")
EVENT_KEY = re.compile(r"^[0-9a-f]{64}(?::(?:block|close|[0-9]{1,15}))?$")
ISSUE = re.compile(r"^[0-9]{8,16}$")
VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
COLLECTOR_WIRE_ID = re.compile(r"^collector-[a-z0-9-]{3,64}$")
SAFE_REASON = re.compile(r"^[a-z0-9_]+$")
BIGINT_MAX = 2**63 - 1
JAVASCRIPT_SAFE_INTEGER = 2**53 - 1
MAX_DATETIME_MILLISECONDS = 253_402_300_799_999
DigestText = Annotated[str, Field(pattern=DIGEST.pattern)]
EventKeyText = Annotated[str, Field(pattern=EVENT_KEY.pattern, max_length=80)]
IssueText = Annotated[str, Field(pattern=ISSUE.pattern)]
VersionText = Annotated[str, Field(pattern=VERSION.pattern)]
SafeReasonText = Annotated[str, Field(pattern=SAFE_REASON.pattern, max_length=64)]
SequenceNumber = Annotated[
    int, Field(strict=True, ge=1, le=JAVASCRIPT_SAFE_INTEGER)
]
NonNegativeWireInteger = Annotated[
    int, Field(strict=True, ge=0, le=JAVASCRIPT_SAFE_INTEGER)
]
WireDatetimeMilliseconds = Annotated[
    int, Field(strict=True, ge=0, le=MAX_DATETIME_MILLISECONDS)
]
WireBoolean = Annotated[bool, Field(strict=True)]


class EventKind(StrEnum):
    BET = "bet"
    CANCEL = "cancel"
    UNATTRIBUTED_CANCEL = "unattributed_cancel"
    CLOSE = "close"
    RESULT = "result"
    CAPTURE_GAP = "capture_gap"
    ISSUE_STATUS = "issue_status"


class NormalizedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    event_key: EventKeyText
    local_sequence: Annotated[int, Field(strict=True, ge=1, le=BIGINT_MAX)]
    actor_key: DigestText | None
    issue: IssueText
    kind: EventKind
    source_ms: Annotated[int, Field(strict=True, ge=0, le=BIGINT_MAX)]
    received_at: AwareDatetime
    play: str | None
    amount_fen: Annotated[int, Field(strict=True, gt=0, le=BIGINT_MAX)] | None
    result_digits: tuple[Annotated[int, Field(strict=True, ge=0, le=9)], ...] | None
    parser_version: VersionText
    gap_reason: SafeReasonText | None = None
    reported_complete: bool | None = None
    reported_reasons: tuple[SafeReasonText, ...] | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        money = self.kind in {EventKind.BET, EventKind.CANCEL}
        if money:
            if self.actor_key is None or self.play is None or self.amount_fen is None:
                raise ValueError("money event requires actor, play and amount")
            if any(
                value is not None
                for value in (
                    self.result_digits,
                    self.gap_reason,
                    self.reported_complete,
                    self.reported_reasons,
                )
            ):
                raise ValueError("money event cannot contain marker data")
            parse_play(self.play)
            return self
        if self.actor_key is not None or self.play is not None or self.amount_fen is not None:
            raise ValueError("marker event cannot contain money identity")
        if self.kind is EventKind.RESULT:
            if self.result_digits is None or len(self.result_digits) != 5:
                raise ValueError("result event requires five digits")
        elif self.result_digits is not None:
            raise ValueError("non-result event cannot contain digits")
        if self.kind is EventKind.CAPTURE_GAP:
            if self.gap_reason is None:
                raise ValueError("capture gap requires a reason")
        elif self.gap_reason is not None:
            raise ValueError("non-gap event cannot contain a gap reason")
        if self.kind is EventKind.ISSUE_STATUS:
            reasons = self.reported_reasons
            if self.reported_complete is None or reasons is None:
                raise ValueError("issue status requires completeness and reasons")
            if len(reasons) > 16:
                raise ValueError("issue status has too many reasons")
            if self.reported_complete and reasons:
                raise ValueError("complete issue status cannot contain reasons")
            if not self.reported_complete and not reasons:
                raise ValueError("incomplete issue status requires a reason")
        elif self.reported_complete is not None or self.reported_reasons is not None:
            raise ValueError("non-status event cannot contain reported status")
        return self


class CollectorBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    collector_id: UUID
    namespace_version: VersionText
    sequence_start: Annotated[int, Field(strict=True, ge=1, le=BIGINT_MAX)]
    sequence_end: Annotated[int, Field(strict=True, ge=1, le=BIGINT_MAX)]
    issue_hint: IssueText
    events: tuple[NormalizedEvent, ...]
    wire_digests: tuple[DigestText, ...] | None = None

    @model_validator(mode="after")
    def validate_sequence(self):
        if not self.events:
            raise ValueError("batch events must be an exact contiguous sequence")
        if self.sequence_end != self.sequence_start + len(self.events) - 1:
            raise ValueError("batch events must be an exact contiguous sequence")
        if any(
            event.local_sequence != self.sequence_start + offset
            for offset, event in enumerate(self.events)
        ):
            raise ValueError("batch events must be an exact contiguous sequence")
        if any(event.issue != self.issue_hint for event in self.events):
            raise ValueError("batch cannot cross issues")
        if self.wire_digests is not None and len(self.wire_digests) != len(self.events):
            raise ValueError("wire digest count must match batch events")
        return self


class BatchAck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    collector_id: UUID
    highest_contiguous_sequence: Annotated[
        int, Field(strict=True, ge=0, le=BIGINT_MAX)
    ]
    accepted_events: Annotated[int, Field(strict=True, ge=0, le=BIGINT_MAX)]
    status: Literal["accepted", "replayed"]


class CollectorWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class CollectorWireEventBase(CollectorWireModel):
    event_key: EventKeyText = Field(alias="eventKey")
    issue: IssueText
    source_ms: NonNegativeWireInteger = Field(alias="sourceMs")
    received_at_ms: WireDatetimeMilliseconds = Field(alias="receivedAtMs")
    source: Literal["realtime", "history"]
    parser_version: Literal["btcffc-1"] = Field(alias="parserVersion")
    namespace_version: Literal["actor-hmac-v1"] = Field(alias="namespaceVersion")


class CollectorBetEvent(CollectorWireEventBase):
    kind: Literal["BET"]
    actor_key: DigestText = Field(alias="actorKey")
    play: str
    amount_minor: Annotated[str, Field(pattern=r"^[1-9][0-9]*$")] = Field(
        alias="amountMinor"
    )

    @model_validator(mode="after")
    def validate_amount(self):
        if int(self.amount_minor) > BIGINT_MAX:
            raise ValueError("amount_minor_out_of_range")
        parse_play(self.play)
        return self


class CollectorCancelEvent(CollectorBetEvent):
    kind: Literal["CANCEL"]


class CollectorUnattributedCancelEvent(CollectorWireEventBase):
    kind: Literal["CANCEL_UNATTRIBUTED"]


class CollectorCloseEvent(CollectorWireEventBase):
    kind: Literal["CLOSE"]


class CollectorResultEvent(CollectorWireEventBase):
    kind: Literal["RESULT"]
    digits: tuple[
        Annotated[int, Field(strict=True, ge=0, le=9)],
        Annotated[int, Field(strict=True, ge=0, le=9)],
        Annotated[int, Field(strict=True, ge=0, le=9)],
        Annotated[int, Field(strict=True, ge=0, le=9)],
        Annotated[int, Field(strict=True, ge=0, le=9)],
    ]


class CollectorCaptureGapEvent(CollectorWireEventBase):
    kind: Literal["CAPTURE_GAP"]
    reason: Literal[
        "decrypt_failure",
        "history_anchor_missing",
        "journal_torn_tail",
        "journal_write_failed",
        "issue_uncertain",
        "cancel_overdraw",
        "opposite_net_conflict",
    ]


class CollectorIssueStatusEvent(CollectorWireEventBase):
    kind: Literal["ISSUE_STATUS"]
    complete: WireBoolean
    reasons: Annotated[tuple[SafeReasonText, ...], Field(max_length=16)]

    @model_validator(mode="after")
    def validate_status(self):
        if self.complete and self.reasons:
            raise ValueError("complete issue status cannot contain reasons")
        if not self.complete and not self.reasons:
            raise ValueError("incomplete issue status requires a reason")
        return self


CollectorWireEvent = Annotated[
    CollectorBetEvent
    | CollectorCancelEvent
    | CollectorUnattributedCancelEvent
    | CollectorCloseEvent
    | CollectorResultEvent
    | CollectorCaptureGapEvent
    | CollectorIssueStatusEvent,
    Field(discriminator="kind"),
]


class CollectorWireRecord(CollectorWireModel):
    seq: SequenceNumber
    event: CollectorWireEvent
    digest: DigestText

    @model_validator(mode="after")
    def validate_digest(self):
        expected = canonical_wire_record_sha256(self.seq, self.event)
        if not hmac.compare_digest(self.digest, expected):
            raise ValueError("wire_digest_mismatch")
        return self


class CollectorWireBatch(CollectorWireModel):
    collector_id: Annotated[str, Field(pattern=COLLECTOR_WIRE_ID.pattern)]
    namespace_version: Literal["actor-hmac-v1"]
    from_seq: SequenceNumber
    to_seq: SequenceNumber
    records: Annotated[tuple[CollectorWireRecord, ...], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def validate_batch(self):
        if self.records[0].seq != self.from_seq or self.records[-1].seq != self.to_seq:
            raise ValueError("batch bounds mismatch")
        if any(
            record.seq != self.from_seq + offset
            for offset, record in enumerate(self.records)
        ):
            raise ValueError("batch is not contiguous")
        issue = self.records[0].event.issue
        if any(record.event.issue != issue for record in self.records):
            raise ValueError("batch cannot cross issues")
        if any(
            record.event.namespace_version != self.namespace_version
            for record in self.records
        ):
            raise ValueError("namespace version mismatch")
        return self


class CollectorSessionRequest(CollectorWireModel):
    collector_id: Annotated[str, Field(pattern=COLLECTOR_WIRE_ID.pattern)]
    namespace_version: Literal["actor-hmac-v1"]


class CollectorSessionResponse(CollectorWireModel):
    ack_seq: NonNegativeWireInteger
    ack_event_key: EventKeyText | None
    history_anchor_event_key: EventKeyText | None
    namespace_empty: WireBoolean


class CollectorWireAck(CollectorWireModel):
    ack_seq: NonNegativeWireInteger


class CollectorHeartbeat(CollectorWireModel):
    collector_id: Annotated[str, Field(pattern=COLLECTOR_WIRE_ID.pattern)]
    issue: IssueText | None
    phase: Literal["BETTING", "CLOSED", "UNKNOWN"]
    countdown_ms: NonNegativeWireInteger
    observed_at_ms: NonNegativeWireInteger
    last_journal_seq: NonNegativeWireInteger
    capture_healthy: WireBoolean


def canonical_event_sha256(event: NormalizedEvent) -> str:
    exclude = {"local_sequence", "received_at"}
    # Keep the pre-wire digest vector stable for the original five kinds.  The
    # two wire-only marker kinds use all three extension fields so a legacy
    # capture-gap digest remains interchangeable with a retransmitted record.
    if event.kind not in {EventKind.CAPTURE_GAP, EventKind.ISSUE_STATUS}:
        exclude.update(("gap_reason", "reported_complete", "reported_reasons"))
    payload = json.dumps(
        event.model_dump(
            mode="json",
            exclude=exclude,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_wire_record_sha256(
    seq: int, event: CollectorWireEvent | dict
) -> str:
    event_payload = (
        event.model_dump(mode="json", by_alias=True)
        if isinstance(event, BaseModel)
        else event
    )
    payload = json.dumps(
        {"seq": seq, "event": event_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
