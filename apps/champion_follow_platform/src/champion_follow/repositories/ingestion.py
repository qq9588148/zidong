from dataclasses import dataclass
from uuid import UUID
from uuid import uuid4

from champion_follow.contracts.events import (
    BatchAck,
    CollectorBatch,
    EventKind,
    NormalizedEvent,
    canonical_event_sha256,
)
from champion_follow.domain.markets import parse_play


class CollectorContractError(Exception):
    pass


class EventConflict(Exception):
    pass


class SequenceGap(Exception):
    def __init__(self, highest_contiguous_sequence: int):
        super().__init__("sequence_gap")
        self.highest_contiguous_sequence = highest_contiguous_sequence


@dataclass(frozen=True)
class GapDetected:
    highest_contiguous_sequence: int


@dataclass(frozen=True)
class CollectorIdentity:
    collector_id: UUID
    wire_id: str


@dataclass(frozen=True)
class CollectorSession:
    ack_sequence: int
    ack_event_key: str | None
    history_anchor_event_key: str | None
    namespace_empty: bool


class IngestionRepository:
    def __init__(self, pool):
        self.pool = pool

    async def authenticate(self, bearer_sha256: str) -> CollectorIdentity | None:
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT id,wire_id FROM collectors WHERE bearer_sha256=%s",
                    (bearer_sha256,),
                )
            ).fetchone()
        if row is None:
            return None
        return CollectorIdentity(collector_id=row["id"], wire_id=row["wire_id"])

    async def collector_session(
        self, collector_id: UUID, namespace_version: str
    ) -> CollectorSession:
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT c.ack_sequence,c.ack_event_key,c.history_anchor_event_key,"
                    "n.version,n.mode,NOT EXISTS("
                    "SELECT 1 FROM source_events s WHERE s.namespace_id=c.namespace_id "
                    "AND s.partition='current' AND s.kind IN ('bet','cancel')"
                    ") AS namespace_empty "
                    "FROM collectors c JOIN identity_namespaces n ON n.id=c.namespace_id "
                    "WHERE c.id=%s",
                    (collector_id,),
                )
            ).fetchone()
        if (
            row is None
            or row["version"] != namespace_version
            or row["mode"] != "active"
        ):
            raise CollectorContractError("namespace_version_mismatch")
        return CollectorSession(
            ack_sequence=row["ack_sequence"],
            ack_event_key=row["ack_event_key"],
            history_anchor_event_key=row["history_anchor_event_key"],
            namespace_empty=row["namespace_empty"],
        )

    async def record_heartbeat(
        self,
        collector_id: UUID,
        *,
        issue: str | None,
        phase: str,
        countdown_ms: int,
        observed_at_ms: int,
        last_journal_sequence: int,
        capture_healthy: bool,
    ) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "INSERT INTO collector_heartbeats(collector_id,issue,phase,countdown_ms,"
                "observed_at_ms,last_journal_sequence,capture_healthy,received_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,clock_timestamp()) "
                "ON CONFLICT (collector_id) DO UPDATE SET issue=EXCLUDED.issue,"
                "phase=EXCLUDED.phase,countdown_ms=EXCLUDED.countdown_ms,"
                "observed_at_ms=EXCLUDED.observed_at_ms,"
                "last_journal_sequence=EXCLUDED.last_journal_sequence,"
                "capture_healthy=EXCLUDED.capture_healthy,received_at=clock_timestamp()",
                (
                    collector_id,
                    issue,
                    phase,
                    countdown_ms,
                    observed_at_ms,
                    last_journal_sequence,
                    capture_healthy,
                ),
            )

    async def ingest(self, batch: CollectorBatch) -> BatchAck | GapDetected:
        result = None
        async with self.pool.connection() as connection:
            async with connection.transaction():
                collector = await self._lock_collector(connection, batch)
                await self._ensure_issue(
                    connection,
                    collector["namespace_id"],
                    batch.issue_hint,
                )

                ack = collector["ack_sequence"]
                if batch.sequence_end <= ack:
                    await self._verify_replay(
                        connection,
                        collector["namespace_id"],
                        batch,
                    )
                    result = BatchAck(
                        collector_id=batch.collector_id,
                        highest_contiguous_sequence=ack,
                        accepted_events=0,
                        status="replayed",
                    )
                elif batch.sequence_start <= ack < batch.sequence_end:
                    raise CollectorContractError("partial_sequence_overlap")
                elif batch.sequence_start > ack + 1:
                    await connection.execute(
                        "INSERT INTO capture_gaps(id,collector_id,from_sequence,to_sequence,"
                        "affected_issue,reason) VALUES (%s,%s,%s,%s,%s,'sequence_gap') "
                        "ON CONFLICT (collector_id,from_sequence,to_sequence) DO NOTHING",
                        (
                            uuid4(),
                            batch.collector_id,
                            ack + 1,
                            batch.sequence_start - 1,
                            batch.issue_hint,
                        ),
                    )
                    result = GapDetected(ack)
                else:
                    anchorable = False
                    for index, event in enumerate(batch.events):
                        anchorable |= await self._store_event(
                            connection,
                            collector,
                            event,
                            batch.wire_digests[index]
                            if batch.wire_digests is not None
                            else None,
                        )
                    if anchorable:
                        await self._advance_history_anchor(
                            connection,
                            batch.collector_id,
                            collector["namespace_id"],
                        )
                    last_event_key = batch.events[-1].event_key
                    updated = await (
                        await connection.execute(
                            "UPDATE collectors SET ack_sequence=%s,ack_event_key=%s "
                            "WHERE id=%s AND ack_sequence=%s RETURNING ack_sequence",
                            (
                                batch.sequence_end,
                                last_event_key,
                                batch.collector_id,
                                ack,
                            ),
                        )
                    ).fetchone()
                    if updated is None:
                        raise EventConflict()
                    await connection.execute(
                        "UPDATE capture_gaps SET recovered_at=now() "
                        "WHERE collector_id=%s AND recovered_at IS NULL AND to_sequence<=%s",
                        (batch.collector_id, batch.sequence_end),
                    )
                    result = BatchAck(
                        collector_id=batch.collector_id,
                        highest_contiguous_sequence=batch.sequence_end,
                        accepted_events=len(batch.events),
                        status="accepted",
                    )

        if result is None:
            raise RuntimeError("ingestion did not produce a result")
        return result

    @staticmethod
    async def _lock_collector(connection, batch: CollectorBatch):
        namespace = await (
            await connection.execute(
                "SELECT id,mode FROM identity_namespaces WHERE version=%s FOR UPDATE",
                (batch.namespace_version,),
            )
        ).fetchone()
        if namespace is None or namespace["mode"] != "active":
            raise CollectorContractError("namespace_version_mismatch")
        collector = await (
            await connection.execute(
                "SELECT c.id AS collector_id,c.namespace_id,c.label,c.parser_version,"
                "c.ack_sequence,c.ack_event_key,c.history_anchor_event_key "
                "FROM collectors c WHERE c.id=%s FOR UPDATE",
                (batch.collector_id,),
            )
        ).fetchone()
        if collector is None:
            raise CollectorContractError("unknown_collector")
        if collector["namespace_id"] != namespace["id"]:
            raise CollectorContractError("namespace_version_mismatch")
        if any(
            event.parser_version != collector["parser_version"]
            for event in batch.events
        ):
            raise CollectorContractError("parser_version_mismatch")
        return collector

    @staticmethod
    async def _ensure_issue(connection, namespace_id, issue: str):
        await connection.execute(
            "INSERT INTO game_issues(issue,issue_no) VALUES (%s,%s) "
            "ON CONFLICT (issue) DO NOTHING",
            (issue, int(issue)),
        )
        await connection.execute(
            "INSERT INTO issue_evaluations(namespace_id,issue) VALUES (%s,%s) "
            "ON CONFLICT (namespace_id,issue) DO NOTHING",
            (namespace_id, issue),
        )

    @staticmethod
    async def _verify_replay(connection, namespace_id, batch: CollectorBatch):
        rows = await (
            await connection.execute(
                "SELECT namespace_id,stream_sequence,event_key,payload_sha256,wire_sha256 "
                "FROM collector_event_receipts "
                "WHERE collector_id=%s AND stream_sequence BETWEEN %s AND %s "
                "ORDER BY stream_sequence",
                (
                    batch.collector_id,
                    batch.sequence_start,
                    batch.sequence_end,
                ),
            )
        ).fetchall()
        if len(rows) != len(batch.events):
            raise EventConflict()
        for index, (row, event) in enumerate(zip(rows, batch.events, strict=True)):
            expected = (
                namespace_id,
                event.local_sequence,
                event.event_key,
                canonical_event_sha256(event),
                batch.wire_digests[index]
                if batch.wire_digests is not None
                else None,
            )
            actual = (
                row["namespace_id"],
                row["stream_sequence"],
                row["event_key"],
                row["payload_sha256"],
                row["wire_sha256"],
            )
            if actual != expected:
                raise EventConflict()

    @staticmethod
    async def _store_event(
        connection,
        collector,
        event: NormalizedEvent,
        wire_sha256: str | None,
    ):
        namespace_id = collector["namespace_id"]
        payload_sha256 = canonical_event_sha256(event)
        parsed = parse_play(event.play) if event.play is not None else None

        if event.actor_key is not None:
            await connection.execute(
                "INSERT INTO anonymous_actors(namespace_id,actor_key,first_seen_at) "
                "VALUES (%s,%s,%s) ON CONFLICT (namespace_id,actor_key) DO NOTHING",
                (namespace_id, event.actor_key, event.received_at),
            )

        source_at_sequence = await (
            await connection.execute(
                "SELECT event_key,payload_sha256 FROM source_events "
                "WHERE collector_id=%s AND stream_sequence=%s",
                (collector["collector_id"], event.local_sequence),
            )
        ).fetchone()
        if source_at_sequence is not None and (
            source_at_sequence["event_key"] != event.event_key
            or source_at_sequence["payload_sha256"] != payload_sha256
        ):
            raise EventConflict()

        inserted = await (
            await connection.execute(
                "INSERT INTO source_events(namespace_id,partition,collector_id,stream_sequence,"
                "event_key,payload_sha256,actor_key,issue,kind,source_ms,received_at,position,"
                "direction,amount_fen,result_digits,gap_reason,reported_complete,"
                "reported_reasons,parser_version,source_label) "
                "VALUES (%s,'current',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING partition,kind,payload_sha256",
                (
                    namespace_id,
                    collector["collector_id"],
                    event.local_sequence,
                    event.event_key,
                    payload_sha256,
                    event.actor_key,
                    event.issue,
                    event.kind.value,
                    event.source_ms,
                    event.received_at,
                    parsed.position if parsed is not None else None,
                    parsed.direction.value if parsed is not None else None,
                    event.amount_fen,
                    list(event.result_digits) if event.result_digits is not None else None,
                    event.gap_reason,
                    event.reported_complete,
                    list(event.reported_reasons)
                    if event.reported_reasons is not None
                    else None,
                    event.parser_version,
                    collector["label"],
                ),
            )
        ).fetchone()
        if inserted is None:
            canonical = await (
                await connection.execute(
                    "SELECT partition,kind,payload_sha256 FROM source_events "
                    "WHERE namespace_id=%s AND event_key=%s",
                    (namespace_id, event.event_key),
                )
            ).fetchone()
            if canonical is None or canonical["payload_sha256"] != payload_sha256:
                raise EventConflict()
        else:
            canonical = inserted

        receipt = await (
            await connection.execute(
                "INSERT INTO collector_event_receipts(namespace_id,collector_id,stream_sequence,"
                "event_key,payload_sha256,wire_sha256) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING "
                "RETURNING namespace_id,event_key,payload_sha256,wire_sha256",
                (
                    namespace_id,
                    collector["collector_id"],
                    event.local_sequence,
                    event.event_key,
                    payload_sha256,
                    wire_sha256,
                ),
            )
        ).fetchone()
        if receipt is None:
            receipt = await (
                await connection.execute(
                    "SELECT namespace_id,event_key,payload_sha256,wire_sha256 "
                    "FROM collector_event_receipts "
                    "WHERE collector_id=%s AND stream_sequence=%s",
                    (collector["collector_id"], event.local_sequence),
                )
            ).fetchone()
        if receipt is None or (
            receipt["namespace_id"] != namespace_id
            or receipt["event_key"] != event.event_key
            or receipt["payload_sha256"] != payload_sha256
            or receipt["wire_sha256"] != wire_sha256
        ):
            raise EventConflict()
        return (
            canonical["partition"] == "current"
            and canonical["kind"] in {EventKind.BET.value, EventKind.CANCEL.value}
        )

    @staticmethod
    async def _advance_history_anchor(connection, collector_id, namespace_id):
        latest = await (
            await connection.execute(
                "SELECT event_key,source_ms FROM source_events "
                "WHERE namespace_id=%s AND partition='current' AND kind IN ('bet','cancel') "
                "ORDER BY source_ms DESC,event_key DESC LIMIT 1",
                (namespace_id,),
            )
        ).fetchone()
        current = await (
            await connection.execute(
                "SELECT s.event_key,s.source_ms FROM collectors c "
                "LEFT JOIN source_events s ON s.namespace_id=c.namespace_id "
                "AND s.event_key=c.history_anchor_event_key WHERE c.id=%s",
                (collector_id,),
            )
        ).fetchone()
        if latest is not None and (
            current["event_key"] is None
            or (latest["source_ms"], latest["event_key"])
            > (current["source_ms"], current["event_key"])
        ):
            await connection.execute(
                "UPDATE collectors SET history_anchor_event_key=%s WHERE id=%s",
                (latest["event_key"], collector_id),
            )
