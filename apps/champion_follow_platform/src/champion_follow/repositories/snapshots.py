import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid4

from champion_follow.domain.markets import ALL_MARKETS, MarketFamily, settle_direction
from champion_follow.domain.statistics import STATISTICS_VERSION
from champion_follow.repositories.profiles import ProfileRepository


SCOPES = ("overall", *ALL_MARKETS)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class SnapshotStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class FrozenCandidate:
    id: UUID
    namespace_id: UUID
    snapshot_id: UUID
    issue: str
    market: str
    actor_key: str
    direction: str
    signal_source_ms: int
    lead_ms: int
    base_rank: int

    @property
    def key(self):
        return self.actor_key, self.market


def _number(value):
    return format(value if isinstance(value, Decimal) else Decimal(value), "f")


def _manifest(scope, statistics_version, ranked_rows):
    entries = [
        [
            rank,
            row["actor_key"],
            row["sample_count"],
            row["wins"],
            row["losses"],
            row["pushes"],
            _number(row["raw_win_rate"]),
            _number(row["all_wilson_lower"]),
            _number(row["recent_wilson_lower"]),
            _number(row["conservative_win_rate"]),
            _number(row["unit_return"]),
            _number(row["conservative_unit_return"]),
            row["blind_count"],
            row["blind_profit_micros"],
            row["blind_max_drawdown_micros"],
            row["level"],
        ]
        for rank, row in ranked_rows
    ]
    canonical = json.dumps(
        {
            "entries": entries,
            "scope": scope,
            "statistics_version": statistics_version,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode()).hexdigest()


class SnapshotRepository:
    def __init__(self, profiles=None, *, statistics_version=STATISTICS_VERSION):
        self.profiles = profiles or ProfileRepository(statistics_version)
        self.statistics_version = statistics_version

    async def freeze_rankings(
        self, connection, namespace_id, issue, last_issue_no
    ):
        issue_row = await (
            await connection.execute(
                "SELECT closed_ms FROM issue_evaluations "
                "WHERE namespace_id=%s AND issue=%s AND integrity_status='complete'",
                (namespace_id, issue),
            )
        ).fetchone()
        if issue_row is None:
            raise SnapshotStateError("complete_issue_not_found")
        frozen_at = EPOCH + timedelta(milliseconds=issue_row["closed_ms"])

        ranked_by_scope = {}
        for scope in SCOPES:
            rows = await self.profiles.ranked_before(
                connection, namespace_id, scope, last_issue_no
            )
            if any(
                row["statistics_version"] != self.statistics_version for row in rows
            ):
                raise SnapshotStateError("profile_statistics_version_mismatch")
            ranked_by_scope[scope] = tuple(enumerate(rows, start=1))

        snapshot_ids_by_scope = {scope: uuid4() for scope in SCOPES}
        for scope in SCOPES:
            snapshot_id = snapshot_ids_by_scope[scope]
            ranked_rows = ranked_by_scope[scope]
            await connection.execute(
                "INSERT INTO ranking_snapshots(id,namespace_id,issue,scope,frozen_at,"
                "statistics_version,manifest_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    snapshot_id,
                    namespace_id,
                    issue,
                    scope,
                    frozen_at,
                    self.statistics_version,
                    _manifest(scope, self.statistics_version, ranked_rows),
                ),
            )
            if ranked_rows:
                async with connection.cursor() as cursor:
                    await cursor.executemany(
                        "INSERT INTO ranking_entries(namespace_id,snapshot_id,actor_key,rank,"
                        "sample_count,wins,losses,pushes,raw_win_rate,all_wilson_lower,"
                        "recent_wilson_lower,conservative_win_rate,unit_return,"
                        "conservative_unit_return,blind_count,blind_profit_micros,"
                        "blind_max_drawdown_micros,level) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [
                            (
                                namespace_id,
                                snapshot_id,
                                row["actor_key"],
                                rank,
                                row["sample_count"],
                                row["wins"],
                                row["losses"],
                                row["pushes"],
                                row["raw_win_rate"],
                                row["all_wilson_lower"],
                                row["recent_wilson_lower"],
                                row["conservative_win_rate"],
                                row["unit_return"],
                                row["conservative_unit_return"],
                                row["blind_count"],
                                row["blind_profit_micros"],
                                row["blind_max_drawdown_micros"],
                                row["level"],
                            )
                            for rank, row in ranked_rows
                        ],
                    )
        return snapshot_ids_by_scope

    async def freeze_candidates(self, connection, snapshot_ids_by_scope, predictions):
        if set(snapshot_ids_by_scope) != set(SCOPES) or len(
            set(snapshot_ids_by_scope.values())
        ) != len(SCOPES):
            raise SnapshotStateError("incomplete_snapshot_mapping")

        snapshots = await (
            await connection.execute(
                "SELECT id,namespace_id,issue,scope,frozen_at,statistics_version "
                "FROM ranking_snapshots WHERE id=ANY(%s)",
                (list(snapshot_ids_by_scope.values()),),
            )
        ).fetchall()
        snapshots_by_id = {row["id"]: row for row in snapshots}
        if any(
            snapshot_ids_by_scope[scope] not in snapshots_by_id
            or snapshots_by_id[snapshot_ids_by_scope[scope]]["scope"] != scope
            for scope in SCOPES
        ):
            raise SnapshotStateError("snapshot_scope_mismatch")
        boundaries = {
            (
                row["namespace_id"],
                row["issue"],
                row["frozen_at"],
                row["statistics_version"],
            )
            for row in snapshots
        }
        if len(boundaries) != 1:
            raise SnapshotStateError("snapshot_boundary_mismatch")
        namespace_id, issue, _, statistics_version = boundaries.pop()
        if statistics_version != self.statistics_version:
            raise SnapshotStateError("snapshot_statistics_version_mismatch")

        issue_row = await (
            await connection.execute(
                "SELECT closed_ms FROM issue_evaluations "
                "WHERE namespace_id=%s AND issue=%s AND integrity_status='complete'",
                (namespace_id, issue),
            )
        ).fetchone()
        if issue_row is None:
            raise SnapshotStateError("complete_issue_not_found")

        candidates = []
        seen = set()
        for prediction in predictions:
            key = (prediction.actor_key, prediction.market)
            if key in seen:
                raise SnapshotStateError("duplicate_prediction")
            seen.add(key)
            if prediction.market not in ALL_MARKETS:
                raise SnapshotStateError("unknown_prediction_market")
            snapshot_id = snapshot_ids_by_scope[prediction.market]
            ranking = await (
                await connection.execute(
                    "SELECT rank,sample_count FROM ranking_entries "
                    "WHERE namespace_id=%s AND snapshot_id=%s AND actor_key=%s",
                    (namespace_id, snapshot_id, prediction.actor_key),
                )
            ).fetchone()
            if ranking is None:
                continue

            lead_ms = issue_row["closed_ms"] - prediction.signal_source_ms
            if lead_ms < 0:
                raise SnapshotStateError("negative_candidate_lead")
            prior_rows = await (
                await connection.execute(
                    "SELECT sample.lead_ms FROM prediction_samples AS sample "
                    "JOIN game_issues AS prior ON prior.issue=sample.issue "
                    "JOIN issue_evaluations AS evaluation ON "
                    "evaluation.namespace_id=sample.namespace_id "
                    "AND evaluation.issue=sample.issue "
                    "AND evaluation.integrity_status='processed' "
                    "JOIN game_issues AS current ON current.issue=%s "
                    "WHERE sample.namespace_id=%s AND sample.actor_key=%s "
                    "AND sample.market=%s AND prior.issue_no<current.issue_no "
                    "ORDER BY prior.issue_no DESC LIMIT %s",
                    (
                        issue,
                        namespace_id,
                        prediction.actor_key,
                        prediction.market,
                        ranking["sample_count"],
                    ),
                )
            ).fetchall()
            prior_lead_times = [row["lead_ms"] for row in reversed(prior_rows)]

            row = await (
                await connection.execute(
                    "INSERT INTO asof_candidates(id,namespace_id,snapshot_id,issue,market,"
                    "actor_key,direction,signal_source_ms,lead_ms,prior_lead_times_ms,"
                    "profile_level,profile_sample_count,profile_wins,profile_losses,"
                    "profile_raw_win_rate,profile_conservative_win_rate,"
                    "profile_conservative_unit_return,base_rank,statistics_version,frozen_at) "
                    "SELECT %s,entry.namespace_id,entry.snapshot_id,snapshot.issue,"
                    "snapshot.scope,entry.actor_key,%s,%s,%s,%s,entry.level,"
                    "entry.sample_count,entry.wins,entry.losses,entry.raw_win_rate,"
                    "entry.conservative_win_rate,entry.conservative_unit_return,entry.rank,"
                    "snapshot.statistics_version,snapshot.frozen_at "
                    "FROM ranking_entries AS entry JOIN ranking_snapshots AS snapshot "
                    "ON snapshot.namespace_id=entry.namespace_id "
                    "AND snapshot.id=entry.snapshot_id WHERE entry.namespace_id=%s "
                    "AND entry.snapshot_id=%s AND entry.actor_key=%s "
                    "AND snapshot.issue=%s AND snapshot.scope=%s "
                    "RETURNING id,namespace_id,snapshot_id,issue,market,actor_key,direction,"
                    "signal_source_ms,lead_ms,base_rank",
                    (
                        uuid4(),
                        prediction.direction,
                        prediction.signal_source_ms,
                        lead_ms,
                        prior_lead_times,
                        namespace_id,
                        snapshot_id,
                        prediction.actor_key,
                        issue,
                        prediction.market,
                    ),
                )
            ).fetchone()
            if row is None:
                raise SnapshotStateError("exact_market_ranking_not_found")
            candidates.append(FrozenCandidate(**row))
        return tuple(candidates)

    async def settle_candidates(self, connection, candidates, result_digits):
        candidates = tuple(candidates)
        if not candidates:
            return
        boundary = {(candidate.namespace_id, candidate.issue) for candidate in candidates}
        if len(boundary) != 1:
            raise SnapshotStateError("candidate_issue_mismatch")
        namespace_id, issue = boundary.pop()
        result_row = await (
            await connection.execute(
                "SELECT result_ms,result_digits FROM issue_evaluations "
                "WHERE namespace_id=%s AND issue=%s AND integrity_status='complete'",
                (namespace_id, issue),
            )
        ).fetchone()
        if result_row is None or tuple(result_row["result_digits"]) != tuple(result_digits):
            raise SnapshotStateError("candidate_result_mismatch")
        settled_at = EPOCH + timedelta(milliseconds=result_row["result_ms"])

        for candidate in candidates:
            position_text, family_text = candidate.market.split(":", 1)
            actual = settle_direction(
                result_digits[int(position_text[1:]) - 1], MarketFamily(family_text)
            ).value
            outcome = 1 if candidate.direction == actual else -1
            unit_profit = 960000 if outcome == 1 else -1000000
            updated = await connection.execute(
                "UPDATE asof_candidates SET outcome=%s,unit_profit_micros=%s,"
                "settled_at=GREATEST(frozen_at,%s) WHERE id=%s AND namespace_id=%s "
                "AND snapshot_id=%s AND outcome IS NULL",
                (
                    outcome,
                    unit_profit,
                    settled_at,
                    candidate.id,
                    candidate.namespace_id,
                    candidate.snapshot_id,
                ),
            )
            if updated.rowcount != 1:
                raise SnapshotStateError("candidate_settlement_conflict")
