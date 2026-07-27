from dataclasses import dataclass

from champion_follow.domain.integrity import Prediction
from champion_follow.domain.markets import MarketFamily, settle_direction
from champion_follow.domain.statistics import STATISTICS_VERSION
from champion_follow.repositories.profiles import ProfileRepository
from champion_follow.repositories.snapshots import SnapshotRepository


class CausalStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessingCursor:
    last_issue_no: int
    last_issue: str | None


class CausalProcessor:
    def __init__(
        self,
        pool,
        *,
        statistics_version=STATISTICS_VERSION,
        profiles=None,
        snapshots=None,
    ):
        self.pool = pool
        self.statistics_version = statistics_version
        self.profiles = profiles or ProfileRepository(statistics_version)
        self.snapshots = snapshots or SnapshotRepository(
            self.profiles,
            statistics_version=statistics_version,
        )

    async def process_ready(self, *, namespace_version):
        namespace_id = await self._namespace_id(namespace_version)
        results = []
        while True:
            next_issue = await self._next_issue(namespace_id)
            if next_issue is None or next_issue["integrity_status"] == "pending":
                break
            if next_issue["integrity_status"] == "processed":
                raise CausalStateError("processed_issue_ahead_of_cursor")
            results.append(await self.process_one(namespace_id, next_issue["issue"]))
        return tuple(results)

    async def process_one(self, namespace_id, issue):
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await self._lock_processing_state(connection, namespace_id)
                if int(issue) <= cursor.last_issue_no:
                    existing = await (
                        await connection.execute(
                            "SELECT 1 FROM issue_evaluations "
                            "WHERE namespace_id=%s AND issue=%s",
                            (namespace_id, issue),
                        )
                    ).fetchone()
                    if existing is None:
                        raise CausalStateError("issue_not_found")
                    return "already_processed"
                issue_row = await self._lock_next_issue(
                    connection,
                    namespace_id,
                    cursor,
                    issue,
                )
                if issue_row is None:
                    return "already_processed"
                if issue_row["integrity_status"] == "pending":
                    raise CausalStateError("issue_not_ready")
                if issue_row["integrity_status"] == "incomplete":
                    await self._advance(connection, namespace_id, issue)
                    return "excluded"
                if issue_row["integrity_status"] != "complete":
                    raise CausalStateError("invalid_integrity_transition")

                predictions = await self._load_predictions(
                    connection,
                    namespace_id,
                    issue,
                )
                snapshot_ids_by_scope = await self.snapshots.freeze_rankings(
                    connection,
                    namespace_id,
                    issue,
                    cursor.last_issue_no,
                )
                candidates = await self.snapshots.freeze_candidates(
                    connection,
                    snapshot_ids_by_scope,
                    predictions,
                )
                await self.snapshots.settle_candidates(
                    connection,
                    candidates,
                    tuple(issue_row["result_digits"]),
                )
                await self._apply_prediction_outcomes(
                    connection,
                    namespace_id,
                    issue,
                    predictions,
                    tuple(issue_row["result_digits"]),
                    {candidate.key for candidate in candidates},
                )
                updated = await connection.execute(
                    "UPDATE issue_evaluations SET integrity_status='processed',"
                    "processed_at=now() WHERE namespace_id=%s AND issue=%s "
                    "AND integrity_status='complete'",
                    (namespace_id, issue),
                )
                if updated.rowcount != 1:
                    raise CausalStateError("integrity_transition_conflict")
                await self._advance(connection, namespace_id, issue)
                return "processed"

    async def _namespace_id(self, namespace_version):
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT id FROM identity_namespaces WHERE version=%s",
                    (namespace_version,),
                )
            ).fetchone()
        if row is None:
            raise CausalStateError("namespace_not_found")
        return row["id"]

    async def _next_issue(self, namespace_id):
        async with self.pool.connection() as connection:
            return await (
                await connection.execute(
                    "SELECT ie.issue,ie.integrity_status FROM issue_evaluations AS ie "
                    "JOIN game_issues AS gi ON gi.issue=ie.issue "
                    "LEFT JOIN processing_state AS ps ON ps.namespace_id=ie.namespace_id "
                    "WHERE ie.namespace_id=%s "
                    "AND gi.issue_no>COALESCE(ps.last_issue_no,0) "
                    "ORDER BY gi.issue_no LIMIT 1",
                    (namespace_id,),
                )
            ).fetchone()

    @staticmethod
    async def _lock_processing_state(connection, namespace_id):
        await connection.execute(
            "INSERT INTO processing_state(namespace_id) VALUES (%s) "
            "ON CONFLICT (namespace_id) DO NOTHING",
            (namespace_id,),
        )
        row = await (
            await connection.execute(
                "SELECT last_issue_no,last_issue FROM processing_state "
                "WHERE namespace_id=%s FOR UPDATE",
                (namespace_id,),
            )
        ).fetchone()
        return ProcessingCursor(int(row["last_issue_no"]), row["last_issue"])

    @staticmethod
    async def _lock_next_issue(connection, namespace_id, cursor, requested_issue):
        row = await (
            await connection.execute(
                "SELECT ie.issue,ie.integrity_status,ie.result_digits "
                "FROM issue_evaluations AS ie "
                "JOIN game_issues AS gi ON gi.issue=ie.issue "
                "WHERE ie.namespace_id=%s AND gi.issue_no>%s "
                "ORDER BY gi.issue_no LIMIT 1 FOR UPDATE OF ie",
                (namespace_id, cursor.last_issue_no),
            )
        ).fetchone()
        if row is None:
            return None
        if row["issue"] != requested_issue:
            raise CausalStateError("issue_out_of_order")
        return row

    @staticmethod
    async def _load_predictions(connection, namespace_id, issue):
        rows = await (
            await connection.execute(
                "SELECT actor_key,market,direction,signal_source_ms,outcome,"
                "unit_profit_micros FROM prediction_samples "
                "WHERE namespace_id=%s AND issue=%s "
                "ORDER BY actor_key,market",
                (namespace_id, issue),
            )
        ).fetchall()
        return tuple(
            Prediction(
                actor_key=row["actor_key"],
                market=row["market"],
                direction=row["direction"],
                signal_source_ms=row["signal_source_ms"],
                outcome=row["outcome"],
                unit_profit_micros=row["unit_profit_micros"],
            )
            for row in rows
        )

    async def _apply_prediction_outcomes(
        self,
        connection,
        namespace_id,
        issue,
        predictions,
        result_digits,
        candidate_keys,
    ):
        for prediction in predictions:
            try:
                position_text, family_text = prediction.market.split(":", 1)
                actual = settle_direction(
                    result_digits[int(position_text[1:]) - 1],
                    MarketFamily(family_text),
                ).value
            except (IndexError, TypeError, ValueError):
                raise CausalStateError("prediction_settlement_mismatch") from None
            outcome = 1 if prediction.direction == actual else -1
            expected_profit = 960000 if outcome == 1 else -1000000
            if (
                prediction.outcome != outcome
                or prediction.unit_profit_micros != expected_profit
            ):
                raise CausalStateError("prediction_settlement_mismatch")
            market_state = await self.profiles.load_for_update(
                connection,
                namespace_id,
                prediction.actor_key,
                prediction.market,
            )
            overall_state = await self.profiles.load_for_update(
                connection,
                namespace_id,
                prediction.actor_key,
                "overall",
            )
            market_state = market_state.observe(outcome)
            overall_state = overall_state.observe(outcome)
            if (prediction.actor_key, prediction.market) in candidate_keys:
                market_state = market_state.observe_blind(outcome)
                overall_state = overall_state.observe_blind(outcome)
            level = overall_state.level
            await self.profiles.save(
                connection,
                namespace_id,
                prediction.actor_key,
                prediction.market,
                market_state,
                market_state.metrics(),
                level,
                issue,
            )
            await self.profiles.save(
                connection,
                namespace_id,
                prediction.actor_key,
                "overall",
                overall_state,
                overall_state.metrics(),
                level,
                issue,
            )

    @staticmethod
    async def _advance(connection, namespace_id, issue):
        updated = await connection.execute(
            "UPDATE processing_state SET last_issue_no=%s,last_issue=%s,updated_at=now() "
            "WHERE namespace_id=%s AND last_issue_no<%s",
            (int(issue), issue, namespace_id, int(issue)),
        )
        if updated.rowcount != 1:
            raise CausalStateError("processing_cursor_conflict")
