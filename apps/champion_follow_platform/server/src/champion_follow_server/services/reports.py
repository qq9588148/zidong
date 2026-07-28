from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Mapping
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select

from champion_follow_server.models.admin import ThresholdConfig
from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AccountStatus,
    Device,
    DeviceStatus,
)
from champion_follow_server.models.device_tasks import (
    DeviceTaskRevision,
    TaskAction,
)
from champion_follow_server.models.ledger import (
    BalanceAvailability,
    BalanceSnapshot,
    BankrollTelemetry,
    LatencySample,
    LatencySegment,
    Order,
    OrderStatus,
    Settlement,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
PERIOD_NAMES = (
    "today",
    "yesterday",
    "week",
    "month",
    "quarter",
    "year",
    "cumulative",
)


@dataclass(frozen=True, slots=True)
class UtcRange:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class PeriodReport:
    turnover_minor: int
    net_pnl_minor: int
    settled_bet_count: int


@dataclass(frozen=True, slots=True)
class LatencyPercentiles:
    p50_ms: int
    p95_ms: int
    p99_ms: int


@dataclass(frozen=True, slots=True)
class LastTaskReport:
    period_id: str
    revision: int
    action: TaskAction
    issued_at: datetime


@dataclass(frozen=True, slots=True)
class LastOrderReport:
    period_id: str
    status: OrderStatus
    stake_minor: int | None
    confirmed_at: datetime | None


@dataclass(frozen=True, slots=True)
class AccountReport:
    account_id: UUID
    generated_at: datetime
    current_balance_minor: int | None
    unrecognized_balance_adjustment_minor: int | None
    periods: Mapping[str, PeriodReport]
    device_id: UUID | None
    device_status: DeviceStatus | None
    device_last_sync_at: datetime | None
    active_threshold_version: int | None
    base_minor: int | None
    cap_minor: int | None
    unrecovered_loss_minor: int | None
    next_stake_minor: int | None
    bankroll_observed_at: datetime | None
    last_task: LastTaskReport | None
    last_order: LastOrderReport | None
    execution_latency: LatencyPercentiles | None


@dataclass(frozen=True, slots=True)
class AdminOverviewReport:
    generated_at: datetime
    user_count: int
    active_device_count: int
    current_balance_minor: int | None
    unrecognized_balance_adjustment_minor: int | None
    periods: Mapping[str, PeriodReport]


@dataclass(frozen=True, slots=True)
class UserListItem:
    account_id: UUID
    username: str
    status: AccountStatus
    created_at: datetime


@dataclass(frozen=True, slots=True)
class UserListPage:
    items: tuple[UserListItem, ...]
    next_cursor: str | None


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("report time must be timezone-aware")
    return value.astimezone(UTC)


def _local_midnight(value: datetime) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=SHANGHAI)


def shanghai_periods(now: datetime) -> dict[str, UtcRange]:
    utc_now = _require_aware(now)
    local_now = utc_now.astimezone(SHANGHAI)
    today_local = _local_midnight(local_now)
    yesterday_local = today_local - timedelta(days=1)
    week_local = today_local - timedelta(days=today_local.weekday())
    month_local = datetime(
        local_now.year, local_now.month, 1, tzinfo=SHANGHAI
    )
    quarter_month = ((local_now.month - 1) // 3) * 3 + 1
    quarter_local = datetime(
        local_now.year, quarter_month, 1, tzinfo=SHANGHAI
    )
    year_local = datetime(local_now.year, 1, 1, tzinfo=SHANGHAI)

    def utc(value: datetime) -> datetime:
        return value.astimezone(UTC)

    return {
        "today": UtcRange(utc(today_local), utc_now),
        "yesterday": UtcRange(utc(yesterday_local), utc(today_local)),
        "week": UtcRange(utc(week_local), utc_now),
        "month": UtcRange(utc(month_local), utc_now),
        "quarter": UtcRange(utc(quarter_local), utc_now),
        "year": UtcRange(utc(year_local), utc_now),
        "cumulative": UtcRange(datetime.min.replace(tzinfo=UTC), utc_now),
    }


class ReportService:
    async def for_account(
        self,
        session,
        *,
        account_id: UUID,
        now: datetime,
    ) -> AccountReport:
        utc_now = _require_aware(now)
        account = await session.scalar(
            select(Account).where(
                Account.id == account_id,
                Account.role == AccountRole.USER,
            )
        )
        if account is None:
            raise LookupError("user account not found")

        periods = {
            name: await self._period(
                session, period, account_id=account_id
            )
            for name, period in shanghai_periods(utc_now).items()
        }
        balance, adjustment = await self._latest_balance(
            session, account_id=account_id, now=utc_now
        )
        device = await self._current_device(session, account_id=account_id)
        threshold_version = None
        bankroll = None
        last_task = None
        last_order = None
        execution_latency = None
        if device is not None:
            threshold_version = await self._effective_threshold_version(
                session, device.id
            )
            bankroll = await session.scalar(
                select(BankrollTelemetry)
                .where(
                    BankrollTelemetry.device_id == device.id,
                    BankrollTelemetry.observed_at <= utc_now,
                )
                .order_by(
                    BankrollTelemetry.observed_at.desc(),
                    BankrollTelemetry.id.desc(),
                )
                .limit(1)
            )
            task = await session.scalar(
                select(DeviceTaskRevision)
                .where(
                    DeviceTaskRevision.device_id == device.id,
                    DeviceTaskRevision.issued_at <= utc_now,
                )
                .order_by(
                    DeviceTaskRevision.issued_at.desc(),
                    DeviceTaskRevision.id.desc(),
                )
                .limit(1)
            )
            if task is not None:
                last_task = LastTaskReport(
                    period_id=task.period_id,
                    revision=task.revision,
                    action=task.action,
                    issued_at=task.issued_at,
                )
            order = await session.scalar(
                select(Order)
                .where(
                    Order.device_id == device.id,
                    Order.created_at <= utc_now,
                )
                .order_by(Order.created_at.desc(), Order.id.desc())
                .limit(1)
            )
            if order is not None:
                last_order = LastOrderReport(
                    period_id=order.period_id,
                    status=order.status,
                    stake_minor=order.stake_minor,
                    confirmed_at=order.confirmed_at,
                )
            execution_latency = await self._latency_percentiles(
                session, device_id=device.id, now=utc_now
            )

        return AccountReport(
            account_id=account.id,
            generated_at=utc_now,
            current_balance_minor=balance,
            unrecognized_balance_adjustment_minor=adjustment,
            periods=periods,
            device_id=device.id if device else None,
            device_status=device.status if device else None,
            device_last_sync_at=device.updated_at if device else None,
            active_threshold_version=threshold_version,
            base_minor=bankroll.base_minor if bankroll else None,
            cap_minor=bankroll.cap_minor if bankroll else None,
            unrecovered_loss_minor=(
                bankroll.unrecovered_loss_minor if bankroll else None
            ),
            next_stake_minor=bankroll.next_stake_minor if bankroll else None,
            bankroll_observed_at=bankroll.observed_at if bankroll else None,
            last_task=last_task,
            last_order=last_order,
            execution_latency=execution_latency,
        )

    async def admin_overview(
        self, session, *, now: datetime
    ) -> AdminOverviewReport:
        utc_now = _require_aware(now)
        user_count = await session.scalar(
            select(func.count(Account.id)).where(Account.role == AccountRole.USER)
        )
        active_device_count = await session.scalar(
            select(func.count(Device.id))
            .join(Account, Account.id == Device.account_id)
            .where(
                Account.role == AccountRole.USER,
                Device.status == DeviceStatus.ACTIVE,
            )
        )
        periods = {
            name: await self._period(session, period, account_id=None)
            for name, period in shanghai_periods(utc_now).items()
        }
        balance, adjustment = await self._latest_balances_for_overview(
            session, utc_now
        )
        return AdminOverviewReport(
            generated_at=utc_now,
            user_count=int(user_count or 0),
            active_device_count=int(active_device_count or 0),
            current_balance_minor=balance,
            unrecognized_balance_adjustment_minor=adjustment,
            periods=periods,
        )

    async def list_users(
        self,
        session,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> UserListPage:
        if limit < 1 or limit > 100:
            raise ValueError("user list limit must be between 1 and 100")
        statement = select(Account).where(Account.role == AccountRole.USER)
        if cursor is not None:
            cursor_time, cursor_id = self._decode_cursor(cursor)
            statement = statement.where(
                or_(
                    Account.created_at > cursor_time,
                    and_(
                        Account.created_at == cursor_time,
                        Account.id > cursor_id,
                    ),
                )
            )
        rows = list(
            (
                await session.scalars(
                    statement.order_by(
                        Account.created_at.asc(), Account.id.asc()
                    ).limit(limit + 1)
                )
            ).all()
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = self._encode_cursor(last.created_at, last.id)
        return UserListPage(
            items=tuple(
                UserListItem(
                    account_id=row.id,
                    username=row.username_canonical,
                    status=row.status,
                    created_at=row.created_at,
                )
                for row in page_rows
            ),
            next_cursor=next_cursor,
        )

    async def _period(
        self,
        session,
        period: UtcRange,
        *,
        account_id: UUID | None,
    ) -> PeriodReport:
        statement = (
            select(
                func.coalesce(func.sum(Order.stake_minor), 0),
                func.coalesce(func.sum(Settlement.net_pnl_minor), 0),
                func.count(Settlement.id),
            )
            .select_from(Order)
            .join(Settlement, Settlement.order_id == Order.id)
            .join(Device, Device.id == Order.device_id)
            .join(Account, Account.id == Device.account_id)
            .where(
                Account.role == AccountRole.USER,
                Order.status == OrderStatus.CONFIRMED,
                Order.stake_minor.is_not(None),
                Order.confirmed_at.is_not(None),
                Order.confirmed_at >= period.start,
                Order.confirmed_at < period.end,
            )
        )
        if account_id is not None:
            statement = statement.where(Account.id == account_id)
        row = (await session.execute(statement)).one()
        return PeriodReport(
            turnover_minor=int(row[0] or 0),
            net_pnl_minor=int(row[1] or 0),
            settled_bet_count=int(row[2] or 0),
        )

    @staticmethod
    async def _latest_balance(
        session, *, account_id: UUID, now: datetime
    ) -> tuple[int | None, int | None]:
        row = await session.scalar(
            select(BalanceSnapshot)
            .join(Device, Device.id == BalanceSnapshot.device_id)
            .where(
                Device.account_id == account_id,
                BalanceSnapshot.observed_at <= now,
            )
            .order_by(
                BalanceSnapshot.observed_at.desc(),
                BalanceSnapshot.id.desc(),
            )
            .limit(1)
        )
        if row is None or row.availability != BalanceAvailability.AVAILABLE:
            return None, None
        return row.balance_minor, row.unrecognized_adjustment_minor

    @staticmethod
    async def _latest_balances_for_overview(
        session, now: datetime
    ) -> tuple[int | None, int | None]:
        ranked = (
            select(
                Device.account_id.label("account_id"),
                BalanceSnapshot.availability.label("availability"),
                BalanceSnapshot.balance_minor.label("balance_minor"),
                BalanceSnapshot.unrecognized_adjustment_minor.label(
                    "unrecognized_adjustment_minor"
                ),
                func.row_number()
                .over(
                    partition_by=Device.account_id,
                    order_by=(
                        BalanceSnapshot.observed_at.desc(),
                        BalanceSnapshot.id.desc(),
                    ),
                )
                .label("row_number"),
            )
            .select_from(BalanceSnapshot)
            .join(Device, Device.id == BalanceSnapshot.device_id)
            .join(Account, Account.id == Device.account_id)
            .where(
                Account.role == AccountRole.USER,
                BalanceSnapshot.observed_at <= now,
            )
            .subquery()
        )
        available = ranked.c.availability == BalanceAvailability.AVAILABLE.value
        result = (
            await session.execute(
                select(
                    func.count(case((available, 1))),
                    func.sum(case((available, ranked.c.balance_minor))),
                    func.sum(
                        case(
                            (
                                available,
                                ranked.c.unrecognized_adjustment_minor,
                            )
                        )
                    ),
                ).where(ranked.c.row_number == 1)
            )
        ).one()
        if int(result[0] or 0) == 0:
            return None, None
        return int(result[1] or 0), int(result[2] or 0)

    @staticmethod
    async def _current_device(session, *, account_id: UUID) -> Device | None:
        return await session.scalar(
            select(Device)
            .where(Device.account_id == account_id)
            .order_by(
                (Device.status == DeviceStatus.ACTIVE).desc(),
                Device.created_at.desc(),
                Device.id.desc(),
            )
            .limit(1)
        )

    @staticmethod
    async def _effective_threshold_version(
        session, device_id: UUID
    ) -> int | None:
        device_row = await session.scalar(
            select(ThresholdConfig)
            .where(
                ThresholdConfig.scope_key == str(device_id),
                ThresholdConfig.is_active.is_(True),
            )
            .order_by(ThresholdConfig.config_version.desc())
            .limit(1)
        )
        if device_row is not None and not device_row.is_removal:
            return device_row.config_version
        global_row = await session.scalar(
            select(ThresholdConfig)
            .where(
                ThresholdConfig.scope_key == "GLOBAL",
                ThresholdConfig.is_active.is_(True),
            )
            .order_by(ThresholdConfig.config_version.desc())
            .limit(1)
        )
        return global_row.config_version if global_row else None

    @staticmethod
    async def _latency_percentiles(
        session, *, device_id: UUID, now: datetime
    ) -> LatencyPercentiles | None:
        result = (
            await session.execute(
                select(
                    func.percentile_disc(0.50).within_group(
                        LatencySample.milliseconds.asc()
                    ),
                    func.percentile_disc(0.95).within_group(
                        LatencySample.milliseconds.asc()
                    ),
                    func.percentile_disc(0.99).within_group(
                        LatencySample.milliseconds.asc()
                    ),
                ).where(
                    LatencySample.device_id == device_id,
                    LatencySample.segment == LatencySegment.SUBMIT_TO_CONFIRM,
                    LatencySample.observed_at <= now,
                )
            )
        ).one()
        if result[0] is None:
            return None
        return LatencyPercentiles(
            p50_ms=int(result[0]),
            p95_ms=int(result[1]),
            p99_ms=int(result[2]),
        )

    @staticmethod
    def _encode_cursor(created_at: datetime, account_id: UUID) -> str:
        payload = json.dumps(
            [created_at.astimezone(UTC).isoformat(), str(account_id)],
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(
                base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
            )
            created_at = _require_aware(datetime.fromisoformat(payload[0]))
            account_id = UUID(payload[1])
        except (ValueError, TypeError, IndexError, json.JSONDecodeError) as exc:
            raise ValueError("invalid user list cursor") from exc
        return created_at, account_id
