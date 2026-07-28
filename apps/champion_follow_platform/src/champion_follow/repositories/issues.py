from uuid import uuid4

from champion_follow.domain.integrity import IssueEvent


class IssueStateError(RuntimeError):
    pass


class IssueRepository:
    def __init__(self, pool):
        self.pool = pool

    async def pending_issues(self, namespace_id):
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT ie.issue FROM issue_evaluations AS ie "
                    "JOIN game_issues AS gi ON gi.issue=ie.issue "
                    "LEFT JOIN processing_state AS ps ON ps.namespace_id=ie.namespace_id "
                    "WHERE ie.namespace_id=%s AND ie.integrity_status='pending' "
                    "AND (ps.last_issue_no IS NULL OR gi.issue_no>ps.last_issue_no) "
                    "ORDER BY gi.issue_no",
                    (namespace_id,),
                )
            ).fetchall()
        return tuple(row["issue"] for row in rows)

    async def finalized_pending_issues(self, namespace_id):
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT ie.issue FROM issue_evaluations AS ie "
                    "JOIN game_issues AS gi ON gi.issue=ie.issue "
                    "WHERE ie.namespace_id=%s AND ie.integrity_status='pending' "
                    "AND EXISTS(SELECT 1 FROM source_events AS event "
                    "WHERE event.namespace_id=ie.namespace_id "
                    "AND event.issue=ie.issue AND event.partition='current' "
                    "AND event.kind='issue_status') "
                    "ORDER BY gi.issue_no",
                    (namespace_id,),
                )
            ).fetchall()
        return tuple(row["issue"] for row in rows)

    async def load_issue_events(self, namespace_id, issue):
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT event_key,kind,actor_key,issue,position,direction,amount_fen,"
                    "source_ms,result_digits,reported_complete,reported_reasons "
                    "FROM source_events WHERE namespace_id=%s AND issue=%s "
                    "AND partition='current' ORDER BY source_ms,event_key",
                    (namespace_id, issue),
                )
            ).fetchall()
        return tuple(
            IssueEvent(
                event_key=row["event_key"],
                kind=row["kind"],
                actor_key=row["actor_key"],
                issue=row["issue"],
                position=row["position"],
                direction=row["direction"],
                amount_fen=row["amount_fen"],
                source_ms=row["source_ms"],
                result_digits=tuple(row["result_digits"])
                if row["result_digits"] is not None
                else None,
                reported_complete=row["reported_complete"],
                reported_reasons=tuple(row["reported_reasons"])
                if row["reported_reasons"] is not None
                else None,
            )
            for row in rows
        )

    async def has_unresolved_gap(self, namespace_id, issue):
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM capture_gaps AS gap "
                    "JOIN collectors AS collector ON collector.id=gap.collector_id "
                    "WHERE collector.namespace_id=%s AND gap.affected_issue=%s "
                    "AND gap.recovered_at IS NULL) AS present",
                    (namespace_id, issue),
                )
            ).fetchone()
        return row["present"]

    async def save_evaluation(self, namespace_id, evaluation, integrity_version):
        reasons = tuple(sorted(set(evaluation.reasons)))
        async with self.pool.connection() as connection:
            async with connection.transaction():
                current = await (
                    await connection.execute(
                        "SELECT integrity_status,integrity_version FROM issue_evaluations "
                        "WHERE namespace_id=%s AND issue=%s FOR UPDATE",
                        (namespace_id, evaluation.issue),
                    )
                ).fetchone()
                if current is None:
                    raise IssueStateError("issue_evaluation_not_found")
                if current["integrity_status"] == "processed":
                    raise IssueStateError("processed_issue_is_immutable")

                status = "complete" if evaluation.complete else "incomplete"
                await connection.execute(
                    "UPDATE issue_evaluations SET closed_ms=%s,result_ms=%s,result_digits=%s,"
                    "integrity_status=%s,integrity_reasons=%s,integrity_version=%s,"
                    "processed_at=NULL WHERE namespace_id=%s AND issue=%s",
                    (
                        evaluation.closed_ms,
                        evaluation.result_ms,
                        list(evaluation.result_digits)
                        if evaluation.result_digits is not None
                        else None,
                        status,
                        list(reasons),
                        integrity_version,
                        namespace_id,
                        evaluation.issue,
                    ),
                )
                if not evaluation.complete:
                    await connection.execute(
                        "DELETE FROM prediction_samples WHERE namespace_id=%s AND issue=%s",
                        (namespace_id, evaluation.issue),
                    )
                    return

                for prediction in evaluation.predictions:
                    await connection.execute(
                        "INSERT INTO prediction_samples(id,namespace_id,actor_key,issue,market,"
                        "direction,signal_source_ms,lead_ms,outcome,unit_profit_micros) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (namespace_id,actor_key,issue,market) DO UPDATE SET "
                        "direction=EXCLUDED.direction,signal_source_ms=EXCLUDED.signal_source_ms,"
                        "lead_ms=EXCLUDED.lead_ms,outcome=EXCLUDED.outcome,"
                        "unit_profit_micros=EXCLUDED.unit_profit_micros",
                        (
                            uuid4(),
                            namespace_id,
                            prediction.actor_key,
                            evaluation.issue,
                            prediction.market,
                            prediction.direction,
                            prediction.signal_source_ms,
                            evaluation.closed_ms - prediction.signal_source_ms,
                            prediction.outcome,
                            prediction.unit_profit_micros,
                        ),
                    )
