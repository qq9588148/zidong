from collections import defaultdict
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import inspect

from champion_follow_server.models.signals import AnonymousActor, AsOfCandidate
from champion_follow_server.services.device_allocator import (
    CandidateMappingError,
    allocation_caps,
    candidate_to_bet_payload,
    device_priority,
)


@pytest.mark.parametrize(
    "device_count,normal,double",
    [
        (1, 1, 1),
        (3, 1, 2),
        (4, 2, 3),
        (9, 2, 3),
        (10, 2, 3),
        (100, 20, 35),
    ],
)
def test_exact_direction_caps(device_count, normal, double) -> None:
    assert allocation_caps(device_count, double_champion=False) == normal
    assert allocation_caps(device_count, double_champion=True) == double


def test_plan01_candidate_mapping_is_explicit_and_read_only() -> None:
    assert set(inspect(AsOfCandidate).columns.keys()) == {
        "id",
        "namespace_id",
        "snapshot_id",
        "issue",
        "market",
        "actor_key",
        "direction",
        "signal_source_ms",
        "lead_ms",
        "prior_lead_times_ms",
        "profile_level",
        "profile_sample_count",
        "profile_wins",
        "profile_losses",
        "profile_raw_win_rate",
        "profile_conservative_win_rate",
        "profile_conservative_unit_return",
        "base_rank",
        "statistics_version",
        "frozen_at",
        "outcome",
        "unit_profit_micros",
        "settled_at",
    }
    assert AsOfCandidate.__table__.info == {
        "schema_owner": "plan01",
        "read_only": True,
    }
    assert AnonymousActor.__table__.info == {
        "schema_owner": "plan01",
        "read_only": True,
    }


def test_three_devices_rotate_first_priority_deterministically() -> None:
    seed = b"a" * 32
    devices = [
        SimpleNamespace(
            id=UUID(f"00000000-0000-0000-0000-00000000000{index}"),
            public_key_fingerprint=bytes([index]) * 32,
        )
        for index in (1, 2, 3)
    ]
    counts = defaultdict(int)
    first_order = []
    for index in range(300):
        issue = f"260727{index:04d}"
        ordered = sorted(
            devices,
            key=lambda device: device_priority(
                device, issue, seed, counts
            ),
        )
        counts[ordered[0].id] += 1
        first_order.append(ordered[0].id)
    assert max(counts.values()) - min(counts.values()) <= 1

    replay_counts = defaultdict(int)
    replay = []
    for index in range(300):
        issue = f"260727{index:04d}"
        ordered = sorted(
            devices,
            key=lambda device: device_priority(
                device, issue, seed, replay_counts
            ),
        )
        replay_counts[ordered[0].id] += 1
        replay.append(ordered[0].id)
    assert replay == first_order


def test_candidate_wire_mapping_rejects_observed_and_mismatched_direction() -> None:
    candidate = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000010"),
        market="P2:parity",
        direction="单",
        profile_level="core",
        profile_sample_count=618,
        profile_conservative_win_rate=Decimal("0.5431000000"),
        profile_conservative_unit_return=Decimal("0.0645000000"),
    )
    payload = candidate_to_bet_payload(
        candidate,
        actor_ref="A000007",
        threshold_version=8,
        followable_rate=Decimal("0.8120000000"),
    )
    assert payload.ball == 2
    assert payload.direction == "ODD"
    assert payload.model_dump()["actor_ref"] == "A000007"

    candidate.profile_level = "observed"
    with pytest.raises(CandidateMappingError):
        candidate_to_bet_payload(
            candidate,
            actor_ref="A000007",
            threshold_version=8,
            followable_rate=Decimal("0.8120000000"),
        )
