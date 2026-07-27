from champion_follow.domain.profiles import ProfileState
from champion_follow.domain.statistics import STATISTICS_VERSION


class ProfileRepository:
    def __init__(self, statistics_version=STATISTICS_VERSION):
        self.statistics_version = statistics_version

    async def load_for_update(
        self, connection, namespace_id, actor_key, market
    ) -> ProfileState:
        row = await (
            await connection.execute(
                "SELECT sample_count,wins,losses,pushes,recent_outcomes,"
                "blind_count,blind_wins,blind_losses,blind_profit_micros,"
                "blind_peak_micros,blind_max_drawdown_micros "
                "FROM actor_profiles WHERE namespace_id=%s AND actor_key=%s "
                "AND scope=%s FOR UPDATE",
                (namespace_id, actor_key, market),
            )
        ).fetchone()
        if row is None:
            return ProfileState.empty()
        return ProfileState(
            sample_count=row["sample_count"],
            wins=row["wins"],
            losses=row["losses"],
            pushes=row["pushes"],
            recent_outcomes=tuple(row["recent_outcomes"]),
            blind_count=row["blind_count"],
            blind_wins=row["blind_wins"],
            blind_losses=row["blind_losses"],
            blind_profit_micros=row["blind_profit_micros"],
            blind_peak_micros=row["blind_peak_micros"],
            blind_max_drawdown_micros=row["blind_max_drawdown_micros"],
        )

    async def save(
        self,
        connection,
        namespace_id,
        actor_key,
        market,
        state,
        metrics,
        level,
        issue,
    ):
        await connection.execute(
            "INSERT INTO actor_profiles("
            "namespace_id,actor_key,scope,sample_count,wins,losses,pushes,"
            "recent_outcomes,raw_win_rate,all_wilson_lower,recent_wilson_lower,"
            "conservative_win_rate,unit_return,conservative_unit_return,"
            "blind_count,blind_wins,blind_losses,blind_profit_micros,"
            "blind_peak_micros,blind_max_drawdown_micros,level,first_seen_at,"
            "last_seen_at,statistics_version,updated_through_issue) VALUES ("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,now(),now(),%s,%s) "
            "ON CONFLICT (namespace_id,actor_key,scope) DO UPDATE SET "
            "sample_count=EXCLUDED.sample_count,wins=EXCLUDED.wins,"
            "losses=EXCLUDED.losses,pushes=EXCLUDED.pushes,"
            "recent_outcomes=EXCLUDED.recent_outcomes,"
            "raw_win_rate=EXCLUDED.raw_win_rate,"
            "all_wilson_lower=EXCLUDED.all_wilson_lower,"
            "recent_wilson_lower=EXCLUDED.recent_wilson_lower,"
            "conservative_win_rate=EXCLUDED.conservative_win_rate,"
            "unit_return=EXCLUDED.unit_return,"
            "conservative_unit_return=EXCLUDED.conservative_unit_return,"
            "blind_count=EXCLUDED.blind_count,blind_wins=EXCLUDED.blind_wins,"
            "blind_losses=EXCLUDED.blind_losses,"
            "blind_profit_micros=EXCLUDED.blind_profit_micros,"
            "blind_peak_micros=EXCLUDED.blind_peak_micros,"
            "blind_max_drawdown_micros=EXCLUDED.blind_max_drawdown_micros,"
            "level=EXCLUDED.level,"
            "first_seen_at=COALESCE(actor_profiles.first_seen_at,EXCLUDED.first_seen_at),"
            "last_seen_at=EXCLUDED.last_seen_at,"
            "statistics_version=EXCLUDED.statistics_version,"
            "updated_through_issue=EXCLUDED.updated_through_issue",
            (
                namespace_id,
                actor_key,
                market,
                state.sample_count,
                state.wins,
                state.losses,
                state.pushes,
                list(state.recent_outcomes),
                metrics.raw_win_rate,
                metrics.all_wilson_lower,
                metrics.recent_wilson_lower,
                metrics.conservative_win_rate,
                metrics.unit_return,
                metrics.conservative_unit_return,
                state.blind_count,
                state.blind_wins,
                state.blind_losses,
                state.blind_profit_micros,
                state.blind_peak_micros,
                state.blind_max_drawdown_micros,
                level,
                self.statistics_version,
                issue,
            ),
        )
        if market == "overall":
            await connection.execute(
                "UPDATE actor_profiles SET level=%s,updated_through_issue=%s "
                "WHERE namespace_id=%s AND actor_key=%s AND scope<>'overall'",
                (level, issue, namespace_id, actor_key),
            )

    async def ranked_before(self, connection, namespace_id, market, issue_no):
        if issue_no is None:
            return ()
        rows = await (
            await connection.execute(
                "SELECT profile.actor_key,profile.sample_count,profile.wins,"
                "profile.losses,profile.pushes,profile.raw_win_rate,"
                "profile.all_wilson_lower,profile.recent_wilson_lower,"
                "profile.conservative_win_rate,profile.unit_return,"
                "profile.conservative_unit_return,profile.blind_count,"
                "profile.blind_profit_micros,profile.blind_max_drawdown_micros,"
                "overall.level,profile.statistics_version "
                "FROM actor_profiles AS profile "
                "JOIN actor_profiles AS overall ON "
                "overall.namespace_id=profile.namespace_id "
                "AND overall.actor_key=profile.actor_key AND overall.scope='overall' "
                "JOIN game_issues AS profile_issue "
                "ON profile_issue.issue=profile.updated_through_issue "
                "JOIN game_issues AS overall_issue "
                "ON overall_issue.issue=overall.updated_through_issue "
                "WHERE profile.namespace_id=%s AND profile.scope=%s "
                "AND profile_issue.issue_no<=%s AND overall_issue.issue_no<=%s "
                "ORDER BY profile.conservative_unit_return DESC,"
                "profile.sample_count DESC,profile.actor_key",
                (namespace_id, market, issue_no, issue_no),
            )
        ).fetchall()
        return tuple(rows)
