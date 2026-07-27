import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select, text, tuple_

from champion_follow_server.models.assignments import (
    AssignmentRound,
    AssignmentState,
    DeviceAssignment,
    PairSequenceCounter,
)
from champion_follow_server.models.signals import AnonymousActor
from champion_follow_server.schemas.device_tasks import BetPayload


MARKET = re.compile(r"^P([1-5]):(size|parity|prime_composite)$")
LEVEL_ORDER = {"candidate": 1, "formal": 2, "core": 3}
WIRE_LEVEL = {"candidate": "CANDIDATE", "formal": "FORMAL", "core": "CORE"}
DIRECTIONS = {
    ("size", "大"): "BIG",
    ("size", "小"): "SMALL",
    ("parity", "单"): "ODD",
    ("parity", "双"): "EVEN",
    ("prime_composite", "质"): "PRIME",
    ("prime_composite", "合"): "COMPOSITE",
}
RATE_QUANTUM = Decimal("0.0000000001")


class CandidateMappingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AllocationResult:
    round: AssignmentRound
    assignments: tuple[DeviceAssignment, ...]
    device_order: tuple[UUID, ...]
    manifest_sha256: bytes


def allocation_caps(device_count: int, *, double_champion: bool) -> int:
    if device_count <= 0:
        return 0
    if device_count <= 3:
        normal, double = 1, min(device_count, 2)
    elif device_count <= 9:
        normal, double = 2, 3
    else:
        normal = max(1, math.floor(Decimal("0.20") * device_count))
        double = max(normal, math.floor(Decimal("0.35") * device_count))
    return double if double_champion else normal


def device_priority(device, issue: str, seed: bytes, prior_first_counts):
    tie = hmac.new(
        seed,
        f"{issue}:{device.public_key_fingerprint.hex()}".encode(),
        hashlib.sha256,
    ).digest()
    return (
        prior_first_counts.get(device.id, 0),
        tie,
        device.public_key_fingerprint,
    )


def _wire_parts(candidate) -> tuple[int, str, str]:
    match = MARKET.fullmatch(candidate.market)
    if match is None:
        raise CandidateMappingError("invalid frozen candidate")
    ball = int(match.group(1))
    family = match.group(2)
    try:
        direction = DIRECTIONS[(family, candidate.direction)]
        level = WIRE_LEVEL[candidate.profile_level]
    except KeyError:
        raise CandidateMappingError("invalid frozen candidate") from None
    return ball, direction, level


def _fixed_rate(value: Decimal) -> str:
    return format(
        Decimal(value).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP), "f"
    )


def candidate_to_bet_payload(
    candidate,
    *,
    actor_ref: str,
    threshold_version: int,
    followable_rate: Decimal,
) -> BetPayload:
    ball, direction, level = _wire_parts(candidate)
    return BetPayload(
        signal_id=candidate.id,
        signal_version=1,
        actor_ref=actor_ref,
        ball=ball,
        direction=direction,
        threshold_version=threshold_version,
        odds_micros=1_960_000,
        user_level=level,
        sample_count=candidate.profile_sample_count,
        conservative_win_rate=_fixed_rate(
            candidate.profile_conservative_win_rate
        ),
        conservative_unit_return=_fixed_rate(
            candidate.profile_conservative_unit_return
        ),
        followable_rate=_fixed_rate(followable_rate),
    )


class DeviceAllocator:
    def __init__(
        self,
        *,
        seed_path: Path,
        seed_version: str,
        threshold_service,
        revision_service,
        clock,
    ) -> None:
        seed = seed_path.read_bytes()
        if len(seed) != 32:
            raise ValueError("allocation seed must contain exactly 32 bytes")
        self._seed = seed
        self._seed_version = seed_version
        self._threshold_service = threshold_service
        self._revision_service = revision_service
        self._clock = clock

    async def allocate(
        self,
        session,
        *,
        issue: str,
        candidates,
        enabled_devices,
    ) -> AllocationResult:
        if not issue or len(issue) > 64:
            raise ValueError("invalid allocation period")
        await session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:allocation_lock, 0))"
            ),
            {"allocation_lock": f"allocation:{issue}"},
        )
        existing = await session.scalar(
            select(AssignmentRound)
            .where(AssignmentRound.period_id == issue)
            .with_for_update()
        )
        if existing is not None:
            assignments = tuple(
                (
                    await session.scalars(
                        select(DeviceAssignment)
                        .where(DeviceAssignment.round_id == existing.id)
                        .order_by(DeviceAssignment.priority_index)
                    )
                ).all()
            )
            return AllocationResult(
                round=existing,
                assignments=assignments,
                device_order=tuple(row.device_id for row in assignments),
                manifest_sha256=existing.manifest_digest,
            )

        devices = tuple(enabled_devices)
        frozen = tuple(candidate for candidate in candidates if candidate.issue == issue)
        counts = {
            device_id: count
            for device_id, count in (
                await session.execute(
                    select(
                        DeviceAssignment.device_id,
                        func.count(DeviceAssignment.id),
                    )
                    .where(DeviceAssignment.priority_index == 0)
                    .group_by(DeviceAssignment.device_id)
                )
            ).all()
        }
        ordered_devices = tuple(
            sorted(
                devices,
                key=lambda device: device_priority(
                    device, issue, self._seed, counts
                ),
            )
        )
        actors = await self._actor_refs(session, frozen)
        plans = []
        direction_counts: dict[tuple[int, str], int] = {}
        selected_by_device: dict[UUID, tuple[int, str]] = {}
        for priority_index, device in enumerate(ordered_devices):
            config = await self._threshold_service.get_effective(
                session, device.id
            )
            if config is None:
                continue
            qualified = []
            for candidate in frozen:
                rate = self._followable_rate(candidate.prior_lead_times_ms)
                if not self._qualifies(candidate, config, rate):
                    continue
                try:
                    ball, direction, _level = _wire_parts(candidate)
                except CandidateMappingError:
                    continue
                qualified.append((candidate, rate, ball, direction))
            qualified.sort(
                key=lambda item: (
                    -item[0].profile_conservative_unit_return,
                    -item[1],
                    -item[0].profile_sample_count,
                    item[0].actor_key,
                    item[0].id,
                )
            )
            chosen = None
            for candidate, rate, ball, direction in qualified:
                double = self._is_double_champion(
                    qualified, ball=ball, direction=direction
                )
                cap = allocation_caps(len(ordered_devices), double_champion=double)
                if direction_counts.get((ball, direction), 0) >= cap:
                    continue
                if not await self._pair_sequence_allows(
                    session,
                    device.id,
                    ball,
                    direction,
                    selected_by_device,
                ):
                    continue
                chosen = (candidate, rate, ball, direction)
                break
            if chosen is None:
                continue
            candidate, rate, ball, direction = chosen
            actor_ref = actors[(candidate.namespace_id, candidate.actor_key)]
            payload = candidate_to_bet_payload(
                candidate,
                actor_ref=actor_ref,
                threshold_version=config.config_version,
                followable_rate=rate,
            )
            plans.append(
                (priority_index, device, candidate, rate, ball, direction, payload)
            )
            direction_counts[(ball, direction)] = (
                direction_counts.get((ball, direction), 0) + 1
            )
            selected_by_device[device.id] = (ball, direction)

        enabled_digest = hashlib.sha256(
            b"".join(
                sorted(device.public_key_fingerprint for device in devices)
            )
        ).digest()
        candidate_digest = hashlib.sha256(
            b"".join(
                candidate.id.bytes
                + candidate.statistics_version.encode("utf-8")
                for candidate in sorted(frozen, key=lambda item: item.id)
            )
        ).digest()
        manifest = [
            {
                "priority_index": priority,
                "device_id": str(device.id),
                "candidate_id": str(candidate.id),
                "statistics_version": candidate.statistics_version,
                "ball": ball,
                "direction": direction,
            }
            for priority, device, candidate, _rate, ball, direction, _payload in plans
        ]
        manifest_digest = hashlib.sha256(
            json.dumps(
                manifest,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).digest()
        round_row = AssignmentRound(
            period_id=issue,
            allocation_seed_version=self._seed_version,
            enabled_device_digest=enabled_digest,
            candidate_snapshot_digest=candidate_digest,
            manifest_digest=manifest_digest,
            created_at=self._clock.now(),
        )
        session.add(round_row)
        await session.flush()
        assignments = []
        expires_at = self._clock.now() + timedelta(minutes=5)
        for priority, device, candidate, rate, ball, direction, payload in plans:
            task = await self._revision_service.publish_bet(
                session,
                device_id=device.id,
                period_id=issue,
                payload=payload,
                expires_at=expires_at,
            )
            assignment = DeviceAssignment(
                round_id=round_row.id,
                device_id=device.id,
                candidate_id=candidate.id,
                candidate_statistics_version=candidate.statistics_version,
                period_id=issue,
                followable_rate=rate,
                priority_index=priority,
                ball=ball,
                direction=direction,
                task_id=task.id,
                task_revision=task.revision,
                execution_state=AssignmentState.PLANNED,
                created_at=self._clock.now(),
                updated_at=self._clock.now(),
            )
            session.add(assignment)
            assignments.append(assignment)
        await session.flush()
        return AllocationResult(
            round=round_row,
            assignments=tuple(assignments),
            device_order=tuple(device.id for device in ordered_devices),
            manifest_sha256=manifest_digest,
        )

    @staticmethod
    def _followable_rate(prior_lead_times_ms) -> Decimal:
        values = tuple(prior_lead_times_ms)
        if not values:
            return Decimal(0).quantize(RATE_QUANTUM)
        return (Decimal(len(values)) / Decimal(len(values))).quantize(
            RATE_QUANTUM
        )

    @staticmethod
    def _qualifies(candidate, config, followable_rate: Decimal) -> bool:
        level = LEVEL_ORDER.get(candidate.profile_level)
        required = LEVEL_ORDER.get(str(config.minimum_level).lower())
        return (
            level is not None
            and required is not None
            and level >= required
            and candidate.profile_conservative_win_rate
            >= config.effective_minimum_win_rate
            and candidate.profile_conservative_unit_return
            >= config.minimum_conservative_roi
            and followable_rate >= config.minimum_followable_rate
        )

    @staticmethod
    def _is_double_champion(qualified, *, ball: int, direction: str) -> bool:
        actors = {
            candidate.actor_key
            for candidate, _rate, candidate_ball, candidate_direction in qualified
            if candidate_ball == ball
            and candidate_direction == direction
            and LEVEL_ORDER.get(candidate.profile_level, 0)
            >= LEVEL_ORDER["formal"]
        }
        return len(actors) >= 2

    @staticmethod
    async def _actor_refs(session, candidates) -> dict[tuple[UUID, str], str]:
        keys = {(candidate.namespace_id, candidate.actor_key) for candidate in candidates}
        if not keys:
            return {}
        rows = (
            await session.execute(
                select(AnonymousActor).where(
                    tuple_(
                        AnonymousActor.namespace_id, AnonymousActor.actor_key
                    ).in_(keys)
                )
            )
        ).scalars()
        refs = {
            (row.namespace_id, row.actor_key): f"A{row.display_no:06d}"
            for row in rows
        }
        if len(refs) != len(keys):
            raise CandidateMappingError("frozen candidate actor is unavailable")
        return refs

    @staticmethod
    async def _pair_sequence_allows(
        session,
        device_id: UUID,
        ball: int,
        direction: str,
        selected: dict[UUID, tuple[int, str]],
    ) -> bool:
        for other_id, other_direction in selected.items():
            if other_direction != (ball, direction):
                continue
            device_a, device_b = sorted((device_id, other_id))
            counter = await session.get(
                PairSequenceCounter, (device_a, device_b)
            )
            if (
                counter is not None
                and counter.identical_count >= 3
                and counter.last_ball == ball
                and counter.last_direction == direction
            ):
                return False
        return True
