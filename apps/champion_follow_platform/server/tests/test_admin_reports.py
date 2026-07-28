from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AccountStatus,
    Device,
    DeviceStatus,
)
from champion_follow_server.models.device_tasks import DeviceTaskRevision, TaskAction
from champion_follow_server.models.ledger import (
    BalanceAvailability,
    BalanceSnapshot,
    BankrollTelemetry,
    DeviceEvent,
    LatencySample,
    LatencySegment,
    Order,
    OrderStatus,
    Settlement,
    SettlementOutcome,
)
from champion_follow_server.services.reports import ReportService, shanghai_periods


def test_shanghai_periods_use_monday_and_calendar_quarter() -> None:
    now = datetime(2026, 7, 27, 1, 30, tzinfo=UTC)
    periods = shanghai_periods(now)

    assert periods["today"].start == datetime(2026, 7, 26, 16, 0, tzinfo=UTC)
    assert periods["yesterday"].start == datetime(2026, 7, 25, 16, 0, tzinfo=UTC)
    assert periods["yesterday"].end == periods["today"].start
    assert periods["week"].start == datetime(2026, 7, 26, 16, 0, tzinfo=UTC)
    assert periods["month"].start == datetime(2026, 6, 30, 16, 0, tzinfo=UTC)
    assert periods["quarter"].start == datetime(2026, 6, 30, 16, 0, tzinfo=UTC)
    assert periods["year"].start == datetime(2025, 12, 31, 16, 0, tzinfo=UTC)


async def _add_event(session, device, *, seq: int, observed_at: datetime) -> DeviceEvent:
    row = DeviceEvent(
        device_id=device.id,
        binding_epoch=device.binding_epoch,
        client_seq=seq,
        event_id=uuid4(),
        event_type="REPORT_FIXTURE",
        observed_at=observed_at,
        received_at=observed_at,
        payload={},
        canonical_payload_digest=bytes([seq % 251]) * 32,
        signature_der=b"fixture-signature",
    )
    session.add(row)
    await session.flush()
    return row


async def _add_settled_order(
    session,
    device,
    *,
    seq: int,
    period_id: str,
    confirmed_at: datetime,
    settled_at: datetime,
    net_pnl_minor: int,
) -> None:
    confirmation_event = await _add_event(
        session,
        device,
        seq=seq * 2 - 1,
        observed_at=confirmed_at,
    )
    task = DeviceTaskRevision(
        device_id=device.id,
        period_id=period_id,
        revision=1,
        action=TaskAction.BET,
        payload={},
        issued_at=confirmed_at - timedelta(seconds=3),
        signing_key_version="test-v1",
        signature=b"s" * 64,
        canonical_sha256=b"c" * 32,
        expires_at=confirmed_at + timedelta(minutes=1),
    )
    session.add(task)
    await session.flush()
    order = Order(
        device_id=device.id,
        task_id=task.id,
        task_revision=1,
        period_id=period_id,
        generation=uuid4(),
        client_order_id=uuid4(),
        platform_order_ref="sha256:" + f"{seq:064x}"[-64:],
        status=OrderStatus.CONFIRMED,
        stake_minor=100,
        confirmation_event_id=confirmation_event.id,
        confirmed_at=confirmed_at,
        created_at=confirmed_at,
        updated_at=confirmed_at,
    )
    session.add(order)
    await session.flush()
    event = await _add_event(
        session, device, seq=seq * 2, observed_at=settled_at
    )
    session.add(
        Settlement(
            order_id=order.id,
            event_id=event.id,
            outcome=(
                SettlementOutcome.WIN
                if net_pnl_minor > 0
                else SettlementOutcome.LOSS
            ),
            net_pnl_minor=net_pnl_minor,
            settled_at=settled_at,
        )
    )
    await session.flush()


@pytest.fixture
def report_now() -> datetime:
    return datetime(2026, 7, 27, 4, 0, tzinfo=UTC)


@pytest.fixture
async def user_with_report_rows(db_session, report_now):
    account = Account(
        username_canonical="report-user",
        password_hash="private-password-hash",
        role=AccountRole.USER,
        status=AccountStatus.ACTIVE,
        admin_slot=None,
    )
    db_session.add(account)
    await db_session.flush()
    device = Device(
        account_id=account.id,
        public_key_spki_der=b"private-public-key",
        public_key_fingerprint=b"r" * 32,
        binding_epoch=1,
        status=DeviceStatus.ACTIVE,
    )
    db_session.add(device)
    await db_session.flush()

    rows = (
        (1, "today", datetime(2026, 7, 26, 16, 30, tzinfo=UTC), 96),
        (2, "yesterday", datetime(2026, 7, 26, 15, 30, tzinfo=UTC), -100),
        (3, "month", datetime(2026, 7, 1, 2, 0, tzinfo=UTC), 96),
        (4, "year", datetime(2026, 6, 30, 2, 0, tzinfo=UTC), -100),
    )
    for seq, period_id, confirmed_at, net_pnl_minor in rows:
        settled_at = confirmed_at + timedelta(minutes=10)
        if period_id == "yesterday":
            settled_at = datetime(2026, 7, 26, 16, 10, tzinfo=UTC)
        await _add_settled_order(
            db_session,
            device,
            seq=seq,
            period_id=period_id,
            confirmed_at=confirmed_at,
            settled_at=settled_at,
            net_pnl_minor=net_pnl_minor,
        )

    balance_event = await _add_event(
        db_session,
        device,
        seq=100,
        observed_at=report_now - timedelta(minutes=1),
    )
    db_session.add(
        BalanceSnapshot(
            event_id=balance_event.id,
            device_id=device.id,
            availability=BalanceAvailability.AVAILABLE,
            balance_minor=12_345,
            unrecognized_adjustment_minor=500,
            observed_at=report_now - timedelta(minutes=1),
        )
    )
    bankroll_event = await _add_event(
        db_session,
        device,
        seq=101,
        observed_at=report_now - timedelta(minutes=2),
    )
    db_session.add(
        BankrollTelemetry(
            event_id=bankroll_event.id,
            device_id=device.id,
            base_minor=100,
            cap_minor=10_000,
            unrecovered_loss_minor=700,
            next_stake_minor=730,
            cycle_id=uuid4(),
            cycle_version=3,
            frozen_reason=None,
            observed_at=report_now - timedelta(minutes=2),
        )
    )
    for offset, milliseconds in enumerate((100, 200, 400), start=102):
        latency_event = await _add_event(
            db_session,
            device,
            seq=offset,
            observed_at=report_now - timedelta(seconds=offset),
        )
        db_session.add(
            LatencySample(
                event_id=latency_event.id,
                device_id=device.id,
                task_id=None,
                segment=LatencySegment.SUBMIT_TO_CONFIRM,
                milliseconds=milliseconds,
                observed_at=report_now - timedelta(seconds=offset),
            )
        )
    await db_session.flush()
    return account, device


@pytest.mark.asyncio
async def test_report_uses_confirmed_period_and_latest_observed_balance(
    db_session, user_with_report_rows, report_now
) -> None:
    account, _device = user_with_report_rows
    report = await ReportService().for_account(
        db_session, account_id=account.id, now=report_now
    )

    assert report.current_balance_minor == 12_345
    assert report.unrecognized_balance_adjustment_minor == 500
    assert asdict(report.periods["today"]) == {
        "turnover_minor": 100,
        "net_pnl_minor": 96,
        "settled_bet_count": 1,
    }
    assert report.periods["yesterday"].net_pnl_minor == -100
    assert report.periods["week"].net_pnl_minor == 96
    assert report.periods["month"].net_pnl_minor == 92
    assert report.periods["quarter"].settled_bet_count == 3
    assert report.periods["year"].net_pnl_minor == -8
    assert report.periods["cumulative"].turnover_minor == 400
    assert report.device_status == DeviceStatus.ACTIVE
    assert report.unrecovered_loss_minor == 700
    assert report.last_task is not None and report.last_task.period_id == "today"
    assert report.last_order is not None and report.last_order.period_id == "today"
    assert report.execution_latency is not None
    assert asdict(report.execution_latency) == {"p50_ms": 200, "p95_ms": 400, "p99_ms": 400}


@pytest.mark.asyncio
async def test_admin_overview_aggregates_the_same_authoritative_values(
    db_session, user_with_report_rows, report_now
) -> None:
    overview = await ReportService().admin_overview(db_session, now=report_now)

    assert overview.user_count >= 1
    assert overview.active_device_count >= 1
    assert overview.current_balance_minor == 12_345
    assert overview.periods["today"].net_pnl_minor == 96
    assert overview.periods["cumulative"].settled_bet_count == 4


@pytest.mark.asyncio
async def test_missing_optional_telemetry_is_null_instead_of_zero(
    db_session, report_now
) -> None:
    account = Account(
        username_canonical="empty-report-user",
        password_hash="private-password-hash",
        role=AccountRole.USER,
        status=AccountStatus.ACTIVE,
        admin_slot=None,
    )
    db_session.add(account)
    await db_session.flush()

    report = await ReportService().for_account(
        db_session, account_id=account.id, now=report_now
    )

    assert report.current_balance_minor is None
    assert report.unrecognized_balance_adjustment_minor is None
    assert report.device_id is None
    assert report.unrecovered_loss_minor is None
    assert report.last_task is None
    assert report.last_order is None
    assert report.execution_latency is None


@pytest.mark.asyncio
async def test_user_listing_uses_stable_created_at_and_id_cursor(
    db_session, report_now
) -> None:
    created_at = report_now - timedelta(days=10)
    first_id = UUID("00000000-0000-0000-0000-000000000701")
    second_id = UUID("00000000-0000-0000-0000-000000000702")
    for account_id, username in (
        (first_id, "cursor-user-a"),
        (second_id, "cursor-user-b"),
    ):
        db_session.add(
            Account(
                id=account_id,
                username_canonical=username,
                password_hash="private-password-hash",
                role=AccountRole.USER,
                status=AccountStatus.ACTIVE,
                admin_slot=None,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    await db_session.flush()
    service = ReportService()

    first_page = await service.list_users(db_session, limit=1)
    second_page = await service.list_users(
        db_session, limit=1, cursor=first_page.next_cursor
    )

    assert [item.account_id for item in first_page.items] == [first_id]
    assert [item.account_id for item in second_page.items] == [second_id]


@pytest.mark.asyncio
async def test_report_dto_excludes_private_auth_and_platform_fields(
    db_session, user_with_report_rows, report_now
) -> None:
    account, _device = user_with_report_rows
    report = await ReportService().for_account(
        db_session, account_id=account.id, now=report_now
    )
    keys: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                keys.add(str(key))
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(asdict(report))
    assert keys.isdisjoint(
        {
            "password_hash",
            "access_digest",
            "refresh_digest",
            "public_key_spki_der",
            "public_key_fingerprint",
            "actor_key",
            "platform_order_ref",
            "platform_cookie",
            "platform_token",
        }
    )
