from uuid import UUID

import pytest
from sqlalchemy import select

from champion_follow_server.models.device_tasks import DeviceTaskRevision
from champion_follow_server.services.device_ledger import (
    DeviceLedgerService,
    OrderConflict,
)


@pytest.mark.asyncio
async def test_one_confirmed_order_per_device_period_and_settlement_is_idempotent(
    auth_session_factory,
    revision_context,
    committed_bet_then_cancel,
    clock,
) -> None:
    device, _threshold = revision_context
    service = DeviceLedgerService(clock)
    async with auth_session_factory() as session:
        task = await session.scalar(
            select(DeviceTaskRevision).where(
                DeviceTaskRevision.device_id == device.id,
                DeviceTaskRevision.period_id == committed_bet_then_cancel.period_id,
                DeviceTaskRevision.revision == 1,
            )
        )
        order = await service.confirm_order(
            session,
            device_id=device.id,
            client_seq=7,
            event_id=UUID("00000000-0000-0000-0000-000000000407"),
            task_id=task.id,
            task_revision=task.revision,
            period_id=task.period_id,
            generation=UUID("00000000-0000-0000-0000-000000000401"),
            client_order_id=UUID("00000000-0000-0000-0000-000000000402"),
            platform_order_ref="sha256:" + "a" * 64,
            stake_minor=100,
            confirmed_at=clock.now(),
        )
        replay = await service.confirm_order(
            session,
            device_id=device.id,
            client_seq=7,
            event_id=UUID("00000000-0000-0000-0000-000000000407"),
            task_id=task.id,
            task_revision=task.revision,
            period_id=task.period_id,
            generation=UUID("00000000-0000-0000-0000-000000000401"),
            client_order_id=UUID("00000000-0000-0000-0000-000000000402"),
            platform_order_ref="sha256:" + "a" * 64,
            stake_minor=100,
            confirmed_at=clock.now(),
        )
        assert replay.id == order.id
        first = await service.settle(
            session,
            order_id=order.id,
            event_id=UUID("00000000-0000-0000-0000-000000000408"),
            client_seq=8,
            outcome="WIN",
            net_pnl_minor=96,
            settled_at=clock.now(),
        )
        second = await service.settle(
            session,
            order_id=order.id,
            event_id=UUID("00000000-0000-0000-0000-000000000408"),
            client_seq=8,
            outcome="WIN",
            net_pnl_minor=96,
            settled_at=clock.now(),
        )
        assert second.id == first.id
        with pytest.raises(OrderConflict):
            await service.confirm_order(
                session,
                device_id=device.id,
                client_seq=9,
                event_id=UUID("00000000-0000-0000-0000-000000000409"),
                task_id=task.id,
                task_revision=task.revision,
                period_id=task.period_id,
                generation=UUID("00000000-0000-0000-0000-000000000401"),
                client_order_id=UUID("00000000-0000-0000-0000-000000000402"),
                platform_order_ref="sha256:" + "a" * 64,
                stake_minor=200,
                confirmed_at=clock.now(),
            )
