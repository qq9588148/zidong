from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from champion_follow_server.models.admin import ThresholdPreview
from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AccountStatus,
    AuthorizationCode,
    CodePurpose,
)


@pytest.mark.asyncio
async def test_only_one_admin_slot_can_exist(db_session) -> None:
    db_session.add_all(
        [
            Account(
                username_canonical="owner-a",
                password_hash="x",
                role=AccountRole.ADMIN,
                status=AccountStatus.ACTIVE,
                admin_slot=1,
            ),
            Account(
                username_canonical="owner-b",
                password_hash="x",
                role=AccountRole.ADMIN,
                status=AccountStatus.ACTIVE,
                admin_slot=1,
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_authorization_code_digest_is_unique(db_session) -> None:
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    first = AuthorizationCode(
        digest=b"a" * 32,
        purpose=CodePurpose.REGISTER,
        expires_at=expires_at,
    )
    second = AuthorizationCode(
        digest=b"a" * 32,
        purpose=CodePurpose.REGISTER,
        expires_at=expires_at,
    )
    db_session.add_all([first, second])
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_0003_already_contains_assignment_and_device_ledger_tables(
    async_engine,
) -> None:
    expected = {
        "admin_threshold_previews",
        "assignment_rounds",
        "device_assignments",
        "pair_sequence_counters",
        "device_event_cursors",
        "device_events",
        "orders",
        "settlements",
        "balance_snapshots",
        "bankroll_telemetry",
        "latency_samples",
    }
    async with async_engine.connect() as connection:
        actual = await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )
    assert expected <= actual


def test_admin_preview_persistence_does_not_shadow_plan01_table() -> None:
    assert ThresholdPreview.__tablename__ == "admin_threshold_previews"
