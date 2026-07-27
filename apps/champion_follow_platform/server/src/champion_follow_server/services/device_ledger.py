import hashlib
import json
import re
from uuid import UUID

from sqlalchemy import select, update

from champion_follow_server.models.auth import Device
from champion_follow_server.models.assignments import (
    AssignmentState,
    DeviceAssignment,
)
from champion_follow_server.models.device_tasks import DeviceTaskRevision, TaskAction
from champion_follow_server.models.ledger import (
    DeviceEvent,
    DeviceEventCursor,
    BalanceAvailability,
    BalanceSnapshot,
    BankrollTelemetry,
    LatencySample,
    LatencySegment,
    Order,
    OrderStatus,
    Settlement,
    SettlementOutcome,
)


PLATFORM_REF = re.compile(r"^sha256:[0-9a-f]{64}$")


class OrderConflict(RuntimeError):
    pass


class EventConflict(RuntimeError):
    pass


class DeviceLedgerService:
    def __init__(self, clock) -> None:
        self._clock = clock

    async def ingest_event(
        self,
        session,
        *,
        device: Device,
        envelope,
        signature_der: bytes,
        canonical_digest: bytes,
    ) -> int:
        cursor = await session.scalar(
            select(DeviceEventCursor)
            .where(DeviceEventCursor.device_id == device.id)
            .with_for_update()
        )
        acknowledged = cursor.acknowledged_client_seq if cursor else 0
        if envelope.binding_epoch != device.binding_epoch:
            raise EventConflict("device event sequence conflict")
        if envelope.client_seq <= acknowledged:
            existing = await session.scalar(
                select(DeviceEvent).where(
                    DeviceEvent.device_id == device.id,
                    DeviceEvent.client_seq == envelope.client_seq,
                )
            )
            if (
                existing is not None
                and existing.canonical_payload_digest == canonical_digest
            ):
                return acknowledged
            raise EventConflict("device event sequence conflict")
        if envelope.client_seq != acknowledged + 1:
            raise EventConflict("device event sequence conflict")
        row = DeviceEvent(
            device_id=device.id,
            binding_epoch=device.binding_epoch,
            client_seq=envelope.client_seq,
            event_id=envelope.event_id,
            event_type=envelope.type,
            observed_at=envelope.observed_at,
            received_at=self._clock.now(),
            payload=envelope.typed_payload().model_dump(mode="json", exclude_none=False),
            canonical_payload_digest=canonical_digest,
            signature_der=signature_der,
        )
        session.add(row)
        await session.flush()
        await self._project_event(session, device, row, envelope)
        if cursor is None:
            cursor = DeviceEventCursor(
                device_id=device.id,
                binding_epoch=device.binding_epoch,
                acknowledged_client_seq=envelope.client_seq,
                last_event_digest=canonical_digest,
                updated_at=self._clock.now(),
            )
            session.add(cursor)
        else:
            cursor.binding_epoch = device.binding_epoch
            cursor.acknowledged_client_seq = envelope.client_seq
            cursor.last_event_digest = canonical_digest
            cursor.updated_at = self._clock.now()
        await session.flush()
        return envelope.client_seq

    async def _project_event(self, session, device, event, envelope) -> None:
        payload = envelope.typed_payload()
        if envelope.type == "TASK_RECEIVED":
            return
        if envelope.type == "EXECUTION_STATE":
            await session.execute(
                update(DeviceAssignment)
                .where(
                    DeviceAssignment.device_id == device.id,
                    DeviceAssignment.period_id == payload.period_id,
                    DeviceAssignment.task_id == payload.task_id,
                    DeviceAssignment.task_revision == payload.revision,
                )
                .values(
                    execution_state=AssignmentState.SUBMITTING,
                    updated_at=self._clock.now(),
                )
            )
            return
        if envelope.type in {
            "ORDER_CONFIRMED",
            "ORDER_REJECTED",
            "ORDER_UNKNOWN",
        }:
            existing = await session.scalar(
                select(Order).where(
                    Order.device_id == device.id,
                    Order.client_order_id == payload.client_order_id,
                )
            )
            if existing is not None:
                return
            task = await session.get(DeviceTaskRevision, payload.task_id)
            if (
                task is None
                or task.device_id != device.id
                or task.period_id != payload.period_id
                or task.revision != payload.task_revision
                or task.action != TaskAction.BET
            ):
                raise OrderConflict("order event conflicts with signed task")
            confirmed = envelope.type == "ORDER_CONFIRMED"
            status = {
                "ORDER_CONFIRMED": OrderStatus.CONFIRMED,
                "ORDER_REJECTED": OrderStatus.REJECTED,
                "ORDER_UNKNOWN": OrderStatus.UNKNOWN,
            }[envelope.type]
            session.add(
                Order(
                    device_id=device.id,
                    task_id=task.id,
                    task_revision=task.revision,
                    period_id=payload.period_id,
                    generation=payload.generation,
                    client_order_id=payload.client_order_id,
                    platform_order_ref=(
                        payload.platform_order_ref if confirmed else None
                    ),
                    status=status,
                    stake_minor=payload.stake_minor if confirmed else None,
                    confirmation_event_id=event.id,
                    confirmed_at=payload.confirmed_at if confirmed else None,
                    created_at=self._clock.now(),
                    updated_at=self._clock.now(),
                )
            )
            await session.flush()
            return
        if envelope.type == "SETTLEMENT_CONFIRMED":
            order = await session.scalar(
                select(Order).where(
                    Order.device_id == device.id,
                    Order.client_order_id == payload.client_order_id,
                    Order.period_id == payload.period_id,
                    Order.status == OrderStatus.CONFIRMED,
                )
            )
            if order is None:
                raise OrderConflict("settlement conflicts with stored state")
            existing = await session.scalar(
                select(Settlement).where(Settlement.order_id == order.id)
            )
            if existing is None:
                session.add(
                    Settlement(
                        order_id=order.id,
                        event_id=event.id,
                        outcome=SettlementOutcome(payload.outcome),
                        net_pnl_minor=payload.net_pnl_minor,
                        settled_at=payload.settled_at,
                    )
                )
                await session.flush()
            return
        if envelope.type == "BALANCE_SNAPSHOT":
            available = payload.availability == "AVAILABLE"
            session.add(
                BalanceSnapshot(
                    event_id=event.id,
                    device_id=device.id,
                    availability=BalanceAvailability(payload.availability),
                    balance_minor=payload.balance_minor,
                    unrecognized_adjustment_minor=0 if available else None,
                    observed_at=envelope.observed_at,
                )
            )
            await session.flush()
            return
        if envelope.type == "BANKROLL_STATE":
            session.add(
                BankrollTelemetry(
                    event_id=event.id,
                    device_id=device.id,
                    base_minor=payload.base_minor,
                    cap_minor=payload.cap_minor,
                    unrecovered_loss_minor=payload.unrecovered_loss_minor,
                    next_stake_minor=payload.next_stake_minor,
                    cycle_id=payload.cycle_id,
                    cycle_version=payload.cycle_version,
                    frozen_reason=payload.frozen_reason,
                    observed_at=envelope.observed_at,
                )
            )
            await session.flush()
            return
        if envelope.type == "LATENCY_SAMPLE":
            session.add(
                LatencySample(
                    event_id=event.id,
                    device_id=device.id,
                    task_id=payload.task_id,
                    segment=LatencySegment(payload.segment),
                    milliseconds=payload.milliseconds,
                    observed_at=envelope.observed_at,
                )
            )
            await session.flush()

    async def confirm_order(
        self,
        session,
        *,
        device_id: UUID,
        client_seq: int,
        event_id: UUID,
        task_id: UUID,
        task_revision: int,
        period_id: str,
        generation: UUID,
        client_order_id: UUID,
        platform_order_ref: str,
        stake_minor: int,
        confirmed_at,
    ) -> Order:
        existing = await session.scalar(
            select(Order).where(
                Order.device_id == device_id,
                Order.client_order_id == client_order_id,
            )
        )
        if existing is not None:
            if (
                existing.task_id == task_id
                and existing.task_revision == task_revision
                and existing.period_id == period_id
                and existing.generation == generation
                and existing.platform_order_ref == platform_order_ref
                and existing.stake_minor == stake_minor
                and existing.status == OrderStatus.CONFIRMED
            ):
                return existing
            raise OrderConflict("order confirmation conflicts with stored state")
        if stake_minor <= 0 or PLATFORM_REF.fullmatch(platform_order_ref) is None:
            raise OrderConflict("order confirmation conflicts with stored state")
        task = await session.get(DeviceTaskRevision, task_id)
        if (
            task is None
            or task.device_id != device_id
            or task.period_id != period_id
            or task.revision != task_revision
            or task.action != TaskAction.BET
        ):
            raise OrderConflict("order confirmation conflicts with stored state")
        event = await self._event(
            session,
            device_id=device_id,
            client_seq=client_seq,
            event_id=event_id,
            event_type="ORDER_CONFIRMED",
            observed_at=confirmed_at,
            payload={
                "task_id": str(task_id),
                "task_revision": task_revision,
                "period_id": period_id,
                "generation": str(generation),
                "client_order_id": str(client_order_id),
                "platform_order_ref": platform_order_ref,
                "stake_minor": stake_minor,
            },
        )
        row = Order(
            device_id=device_id,
            task_id=task_id,
            task_revision=task_revision,
            period_id=period_id,
            generation=generation,
            client_order_id=client_order_id,
            platform_order_ref=platform_order_ref,
            status=OrderStatus.CONFIRMED,
            stake_minor=stake_minor,
            confirmation_event_id=event.id,
            confirmed_at=confirmed_at,
            created_at=self._clock.now(),
            updated_at=self._clock.now(),
        )
        session.add(row)
        await session.flush()
        return row

    async def settle(
        self,
        session,
        *,
        order_id: UUID,
        event_id: UUID,
        client_seq: int,
        outcome: SettlementOutcome | str,
        net_pnl_minor: int,
        settled_at,
    ) -> Settlement:
        normalized = SettlementOutcome(outcome)
        existing = await session.scalar(
            select(Settlement).where(Settlement.order_id == order_id)
        )
        if existing is not None:
            stored_event = await session.get(DeviceEvent, existing.event_id)
            if (
                stored_event is not None
                and stored_event.event_id == event_id
                and existing.outcome == normalized
                and existing.net_pnl_minor == net_pnl_minor
                and existing.settled_at == settled_at
            ):
                return existing
            raise OrderConflict("settlement conflicts with stored state")
        order = await session.get(Order, order_id)
        if order is None or order.status != OrderStatus.CONFIRMED:
            raise OrderConflict("settlement conflicts with stored state")
        event = await self._event(
            session,
            device_id=order.device_id,
            client_seq=client_seq,
            event_id=event_id,
            event_type="SETTLEMENT_CONFIRMED",
            observed_at=settled_at,
            payload={
                "client_order_id": str(order.client_order_id),
                "period_id": order.period_id,
                "outcome": normalized.value,
                "net_pnl_minor": net_pnl_minor,
            },
        )
        row = Settlement(
            order_id=order.id,
            event_id=event.id,
            outcome=normalized,
            net_pnl_minor=net_pnl_minor,
            settled_at=settled_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def _event(
        self,
        session,
        *,
        device_id: UUID,
        client_seq: int,
        event_id: UUID,
        event_type: str,
        observed_at,
        payload: dict,
    ) -> DeviceEvent:
        existing = await session.scalar(
            select(DeviceEvent).where(
                DeviceEvent.device_id == device_id,
                DeviceEvent.event_id == event_id,
            )
        )
        if existing is not None:
            return existing
        sequence = await session.scalar(
            select(DeviceEvent).where(
                DeviceEvent.device_id == device_id,
                DeviceEvent.client_seq == client_seq,
            )
        )
        if sequence is not None:
            raise EventConflict("device event sequence conflict")
        device = await session.get(Device, device_id)
        if device is None or client_seq < 1:
            raise EventConflict("device event sequence conflict")
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        row = DeviceEvent(
            device_id=device_id,
            binding_epoch=device.binding_epoch,
            client_seq=client_seq,
            event_id=event_id,
            event_type=event_type,
            observed_at=observed_at,
            received_at=self._clock.now(),
            payload=payload,
            canonical_payload_digest=hashlib.sha256(canonical).digest(),
            signature_der=b"test-projected-event",
        )
        session.add(row)
        await session.flush()
        return row
