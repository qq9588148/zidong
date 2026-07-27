from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from champion_follow.contracts import events as events_module
from champion_follow.contracts.events import (
    BatchAck,
    CollectorBatch,
    EventKind,
    NormalizedEvent,
    canonical_event_sha256,
)


COLLECTOR = UUID("11111111-1111-4111-8111-111111111111")
ACTOR = "a" * 64
EVENT = "b" * 64 + ":0"
BIGINT_MAX = 2**63 - 1


def bet_event(**changes):
    value = {
        "event_key": EVENT,
        "local_sequence": 41,
        "actor_key": ACTOR,
        "issue": "2607270001",
        "kind": EventKind.BET,
        "source_ms": 1_785_084_000_000,
        "received_at": datetime(2026, 7, 27, tzinfo=timezone.utc),
        "play": "P1:大",
        "amount_fen": 250,
        "result_digits": None,
        "parser_version": "ffc-normalizer-v2",
    }
    value.update(changes)
    return value


def result_event(**changes):
    value = bet_event(
        actor_key=None,
        kind=EventKind.RESULT,
        play=None,
        amount_fen=None,
        result_digits=(1, 2, 3, 4, 5),
    )
    value.update(changes)
    return value


def batch(**changes):
    value = {
        "collector_id": COLLECTOR,
        "namespace_version": "actor-hmac-v1",
        "sequence_start": 41,
        "sequence_end": 41,
        "issue_hint": "2607270001",
        "events": [bet_event()],
    }
    value.update(changes)
    return value


def ack(**changes):
    value = {
        "collector_id": COLLECTOR,
        "highest_contiguous_sequence": 41,
        "accepted_events": 1,
        "status": "accepted",
    }
    value.update(changes)
    return value


def test_event_contract_accepts_only_normalized_anonymous_money():
    event = NormalizedEvent.model_validate(bet_event())
    assert event.actor_key == ACTOR
    assert event.amount_fen == 250
    assert len(canonical_event_sha256(event)) == 64


def test_event_contract_accepts_a_numeric_legacy_parser_version():
    event = NormalizedEvent.model_validate(bet_event(parser_version="7"))

    assert event.parser_version == "7"


def test_canonical_event_digest_is_locked_and_mapping_order_independent():
    payload = bet_event()
    reversed_payload = dict(reversed(tuple(payload.items())))

    assert canonical_event_sha256(NormalizedEvent.model_validate(payload)) == (
        "3516f50c99f9d2798e8b2733aaf6c492ee9ec5d97f894ceab44c00a75b8c275f"
    )
    assert canonical_event_sha256(NormalizedEvent.model_validate(reversed_payload)) == (
        "3516f50c99f9d2798e8b2733aaf6c492ee9ec5d97f894ceab44c00a75b8c275f"
    )


def test_canonical_event_digest_excludes_transport_sequence_and_receipt_time():
    original = NormalizedEvent.model_validate(bet_event())
    retransmitted = NormalizedEvent.model_validate(
        bet_event(
            local_sequence=99,
            received_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        )
    )

    assert canonical_event_sha256(retransmitted) == canonical_event_sha256(original)


@pytest.mark.parametrize(
    "private_field",
    ["uid", "nickname", "cookie", "token", "password", "authorization", "platform_actor_id"],
)
def test_raw_identity_and_credentials_are_rejected(private_field):
    with pytest.raises(ValidationError) as raised:
        NormalizedEvent.model_validate({**bet_event(), private_field: "PRIVATE"})
    assert "Extra inputs are not permitted" in str(raised.value)
    assert "PRIVATE" not in str(raised.value)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (CollectorBatch, batch(private_field="PRIVATE")),
        (BatchAck, ack(private_field="PRIVATE")),
    ],
)
def test_batch_dto_errors_hide_private_extra_values(model, payload):
    with pytest.raises(ValidationError) as raised:
        model.model_validate(payload)
    assert "PRIVATE" not in str(raised.value)


def test_money_and_result_shapes_cannot_be_mixed():
    with pytest.raises(ValidationError, match="money event"):
        NormalizedEvent.model_validate(bet_event(result_digits=(1, 2, 3, 4, 5)))
    with pytest.raises(ValidationError, match="result event"):
        NormalizedEvent.model_validate(bet_event(
            kind=EventKind.RESULT,
            actor_key=None,
            play=None,
            amount_fen=None,
            result_digits=(1, 2, 3, 4),
        ))


def test_batch_requires_an_exact_contiguous_sequence():
    with pytest.raises(ValidationError, match="contiguous"):
        CollectorBatch.model_validate({
            "collector_id": COLLECTOR,
            "namespace_version": "actor-hmac-v1",
            "sequence_start": 41,
            "sequence_end": 42,
            "issue_hint": "2607270001",
            "events": [bet_event()],
        })


def test_batch_rejects_a_huge_declared_span_without_materializing_range(monkeypatch):
    def forbidden_range(*args):
        raise AssertionError("range must not be called")

    monkeypatch.setattr(events_module, "range", forbidden_range, raising=False)

    with pytest.raises(ValidationError, match="contiguous"):
        CollectorBatch.model_validate(batch(sequence_start=1, sequence_end=BIGINT_MAX))


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("local_sequence", True),
        ("local_sequence", "41"),
        ("local_sequence", 41.0),
        ("source_ms", True),
        ("source_ms", "1785084000000"),
        ("source_ms", 1_785_084_000_000.0),
        ("amount_fen", True),
        ("amount_fen", "250"),
        ("amount_fen", 250.0),
    ],
)
def test_event_integer_fields_reject_coercion(field, invalid):
    with pytest.raises(ValidationError):
        NormalizedEvent.model_validate(bet_event(**{field: invalid}))


@pytest.mark.parametrize("invalid", [True, "1", 1.0])
def test_result_digits_reject_coercion(invalid):
    with pytest.raises(ValidationError):
        NormalizedEvent.model_validate(result_event(result_digits=(invalid, 2, 3, 4, 5)))


@pytest.mark.parametrize("field", ["local_sequence", "source_ms", "amount_fen"])
def test_event_db_integer_fields_reject_bigint_overflow(field):
    with pytest.raises(ValidationError):
        NormalizedEvent.model_validate(bet_event(**{field: BIGINT_MAX + 1}))


@pytest.mark.parametrize("field", ["sequence_start", "sequence_end"])
@pytest.mark.parametrize("invalid", [True, "41", 41.0, BIGINT_MAX + 1])
def test_batch_sequence_fields_are_strict_and_bigint_bounded(field, invalid):
    with pytest.raises(ValidationError):
        CollectorBatch.model_validate(batch(**{field: invalid}))


@pytest.mark.parametrize("field", ["highest_contiguous_sequence", "accepted_events"])
@pytest.mark.parametrize("invalid", [-1, True, "1", 1.0, BIGINT_MAX + 1])
def test_ack_counts_are_strict_non_negative_bigint_bounded(field, invalid):
    with pytest.raises(ValidationError):
        BatchAck.model_validate(ack(**{field: invalid}))


@pytest.mark.parametrize("status", ["rejected", "ACCEPTED", "accepted ", 1])
def test_ack_rejects_unknown_status(status):
    with pytest.raises(ValidationError):
        BatchAck.model_validate(ack(status=status))


@pytest.mark.parametrize("status", ["accepted", "replayed"])
def test_ack_accepts_frozen_statuses(status):
    assert BatchAck.model_validate(ack(status=status)).status == status


@pytest.mark.parametrize("suffix", ["١", "1" * 16])
def test_event_key_rejects_non_ascii_or_oversized_numeric_suffix(suffix):
    with pytest.raises(ValidationError):
        NormalizedEvent.model_validate(bet_event(event_key="b" * 64 + ":" + suffix))
