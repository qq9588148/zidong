import re
from decimal import Decimal, ROUND_HALF_UP

from champion_follow.contracts.rankings import (
    RankingEntryResponse,
    RankingResponse,
)
from champion_follow.domain.markets import ALL_MARKETS


MARKET = re.compile(r"^P[1-5]:(size|parity|prime_composite)$", re.IGNORECASE)
RETURN_QUANTUM = Decimal("0.000000000001")
MICROS = Decimal(1_000_000)


class RankingNotFound(RuntimeError):
    pass


def normalize_scope(value: str) -> str:
    if value.lower() == "overall":
        return "overall"
    if MARKET.fullmatch(value) is None:
        raise RankingNotFound("ranking_not_found")
    position, family = value.split(":", 1)
    scope = f"{position.upper()}:{family.lower()}"
    if scope not in ALL_MARKETS:
        raise RankingNotFound("ranking_not_found")
    return scope


def _blind_unit_return(profit_micros: int, count: int) -> Decimal:
    if count <= 0:
        return Decimal(0).quantize(RETURN_QUANTUM)
    return (Decimal(profit_micros) / Decimal(count) / MICROS).quantize(
        RETURN_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


class RankingService:
    def __init__(self, pool):
        self.pool = pool

    async def get(self, market: str, *, as_of_issue: str | None = None):
        scope = normalize_scope(market)
        parameters = [scope]
        issue_filter = ""
        if as_of_issue is not None:
            issue_filter = "AND snapshot.issue=%s "
            parameters.append(as_of_issue)
        async with self.pool.connection() as connection:
            snapshot = await (
                await connection.execute(
                    "SELECT snapshot.id,snapshot.namespace_id,snapshot.issue,"
                    "snapshot.scope,snapshot.frozen_at,snapshot.statistics_version "
                    "FROM ranking_snapshots AS snapshot "
                    "JOIN identity_namespaces AS namespace "
                    "ON namespace.id=snapshot.namespace_id AND namespace.mode='active' "
                    "JOIN game_issues AS issue ON issue.issue=snapshot.issue "
                    "WHERE snapshot.scope=%s "
                    + issue_filter
                    + "ORDER BY issue.issue_no DESC LIMIT 1",
                    parameters,
                )
            ).fetchone()
            if snapshot is None:
                raise RankingNotFound("ranking_not_found")
            rows = await (
                await connection.execute(
                    "SELECT actor.display_no,entry.rank,entry.level,"
                    "entry.sample_count,entry.raw_win_rate,"
                    "entry.conservative_win_rate,entry.unit_return,"
                    "entry.conservative_unit_return,entry.blind_count,"
                    "entry.blind_profit_micros "
                    "FROM ranking_entries AS entry "
                    "JOIN anonymous_actors AS actor "
                    "ON actor.namespace_id=entry.namespace_id "
                    "AND actor.actor_key=entry.actor_key "
                    "WHERE entry.namespace_id=%s AND entry.snapshot_id=%s "
                    "ORDER BY entry.rank",
                    (snapshot["namespace_id"], snapshot["id"]),
                )
            ).fetchall()

        entries = tuple(
            RankingEntryResponse(
                actor_ref=f"A{row['display_no']:06d}",
                market=scope,
                rank=row["rank"],
                level=row["level"],
                sample_count=row["sample_count"],
                raw_win_rate=row["raw_win_rate"],
                conservative_win_rate=row["conservative_win_rate"],
                unit_return=row["unit_return"],
                conservative_unit_return=row["conservative_unit_return"],
                blind_count=row["blind_count"],
                blind_unit_return=_blind_unit_return(
                    row["blind_profit_micros"],
                    row["blind_count"],
                ),
            )
            for row in rows
        )
        return RankingResponse(
            market=scope,
            issue=snapshot["issue"],
            frozen_at=snapshot["frozen_at"],
            statistics_version=snapshot["statistics_version"],
            entries=entries,
        )
