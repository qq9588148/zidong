import base64
import hashlib
from datetime import timedelta
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from sqlalchemy import select, text

from champion_follow_server.models.admin import GlobalControl, ThresholdConfig
from champion_follow_server.models.auth import (
    Account,
    AccountStatus,
    Device,
    DeviceStatus,
)
from champion_follow_server.models.device_tasks import (
    DeviceTaskHead,
    DeviceTaskRevision,
    TaskAction,
)
from champion_follow_server.schemas.device_tasks import (
    BetPayload,
    CancelPayload,
    CancelReason,
    SignedTaskEnvelope,
    utc_rfc3339,
)
from champion_follow_server.security.task_signing import (
    TaskSigner,
    canonical_task_bytes,
)


class TaskUnavailable(RuntimeError):
    pass


class TaskIntegrityError(RuntimeError):
    pass


class DeviceTaskRevisionService:
    def __init__(self, signer: TaskSigner, clock) -> None:
        self._signer = signer
        self._clock = clock

    async def publish_bet(
        self,
        session,
        *,
        device_id: UUID,
        period_id: str,
        payload: BetPayload,
        expires_at,
    ) -> DeviceTaskRevision:
        await self._validate_executable(session, device_id, payload)
        return await self._publish(
            session,
            device_id=device_id,
            period_id=period_id,
            action=TaskAction.BET,
            payload=payload,
            expires_at=expires_at,
        )

    async def publish_cancel(
        self,
        session,
        *,
        device_id: UUID,
        period_id: str,
        reason: CancelReason | str,
        expires_at,
    ) -> DeviceTaskRevision:
        payload = CancelPayload(reason=reason)
        return await self._publish(
            session,
            device_id=device_id,
            period_id=period_id,
            action=TaskAction.CANCEL,
            payload=payload,
            expires_at=expires_at,
        )

    async def current_head(
        self, session, device_id: UUID, period_id: str
    ) -> DeviceTaskRevision | None:
        row = await session.scalar(
            select(DeviceTaskRevision)
            .join(
                DeviceTaskHead,
                DeviceTaskHead.task_id == DeviceTaskRevision.id,
            )
            .where(
                DeviceTaskHead.device_id == device_id,
                DeviceTaskHead.period_id == period_id,
            )
        )
        if row is not None:
            self.signed_envelope(row)
        return row

    async def cancel_live_bets(
        self,
        session,
        *,
        reason: CancelReason | str,
        device_ids: set[UUID] | None = None,
        expires_at=None,
    ) -> list[DeviceTaskRevision]:
        statement = (
            select(DeviceTaskRevision)
            .join(
                DeviceTaskHead,
                DeviceTaskHead.task_id == DeviceTaskRevision.id,
            )
            .where(DeviceTaskRevision.action == TaskAction.BET)
        )
        if device_ids is not None:
            statement = statement.where(
                DeviceTaskRevision.device_id.in_(device_ids)
            )
        live = tuple((await session.scalars(statement)).all())
        deadline = expires_at or self._clock.now() + timedelta(minutes=5)
        return [
            await self.publish_cancel(
                session,
                device_id=row.device_id,
                period_id=row.period_id,
                reason=reason,
                expires_at=deadline,
            )
            for row in live
        ]

    def signed_envelope(
        self, row: DeviceTaskRevision
    ) -> SignedTaskEnvelope:
        unsigned = self._unsigned(
            task_id=row.id,
            device_id=row.device_id,
            period_id=row.period_id,
            revision=row.revision,
            action=row.action,
            issued_at=row.issued_at,
            expires_at=row.expires_at,
            payload=row.payload,
        )
        canonical = canonical_task_bytes(unsigned)
        if not hashlib.sha256(canonical).digest() == row.canonical_sha256:
            raise TaskIntegrityError("stored task integrity check failed")
        try:
            self._signer.public_key.verify(row.signature, canonical)
        except InvalidSignature:
            raise TaskIntegrityError("stored task integrity check failed") from None
        return SignedTaskEnvelope(
            **unsigned,
            signature=base64.urlsafe_b64encode(row.signature).decode("ascii"),
        )

    def wire_envelope(self, row: DeviceTaskRevision) -> dict:
        envelope = self.signed_envelope(row)
        return {
            **self._unsigned(
                task_id=row.id,
                device_id=row.device_id,
                period_id=row.period_id,
                revision=row.revision,
                action=row.action,
                issued_at=row.issued_at,
                expires_at=row.expires_at,
                payload=row.payload,
            ),
            "signature": envelope.signature,
        }

    async def _publish(
        self,
        session,
        *,
        device_id: UUID,
        period_id: str,
        action: TaskAction,
        payload: BetPayload | CancelPayload,
        expires_at,
    ) -> DeviceTaskRevision:
        if not period_id or len(period_id) > 64:
            raise TaskUnavailable("task unavailable")
        issued_at = self._clock.now()
        if expires_at <= issued_at:
            raise TaskUnavailable("task unavailable")
        await session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:task_lock, 0))"
            ),
            {"task_lock": f"task:{device_id}:{period_id}"},
        )
        head = await session.scalar(
            select(DeviceTaskHead)
            .where(
                DeviceTaskHead.device_id == device_id,
                DeviceTaskHead.period_id == period_id,
            )
            .with_for_update()
        )
        if head is not None and action == TaskAction.CANCEL:
            current = await session.get(DeviceTaskRevision, head.task_id)
            if (
                current is not None
                and current.action == TaskAction.CANCEL
                and current.payload == payload.model_dump(mode="json")
            ):
                return current
        revision = 1 if head is None else head.revision + 1
        task_id = uuid4()
        payload_data = payload.model_dump(mode="json")
        unsigned = self._unsigned(
            task_id=task_id,
            device_id=device_id,
            period_id=period_id,
            revision=revision,
            action=action,
            issued_at=issued_at,
            expires_at=expires_at,
            payload=payload_data,
        )
        canonical = canonical_task_bytes(unsigned)
        signature = self._signer.sign(unsigned)
        row = DeviceTaskRevision(
            id=task_id,
            device_id=device_id,
            period_id=period_id,
            revision=revision,
            action=action,
            payload=payload_data,
            issued_at=issued_at,
            signing_key_version=self._signer.key_version,
            signature=signature,
            canonical_sha256=hashlib.sha256(canonical).digest(),
            expires_at=expires_at,
        )
        session.add(row)
        await session.flush()
        if head is None:
            session.add(
                DeviceTaskHead(
                    device_id=device_id,
                    period_id=period_id,
                    revision=revision,
                    task_id=row.id,
                )
            )
        else:
            head.revision = revision
            head.task_id = row.id
        await session.flush()
        return row

    async def _validate_executable(
        self, session, device_id: UUID, payload: BetPayload
    ) -> None:
        result = (
            await session.execute(
                select(Device, Account)
                .join(Account, Account.id == Device.account_id)
                .where(Device.id == device_id)
            )
        ).one_or_none()
        if result is None:
            raise TaskUnavailable("task unavailable")
        device, account = result
        if (
            device.status != DeviceStatus.ACTIVE
            or account.status != AccountStatus.ACTIVE
        ):
            raise TaskUnavailable("task unavailable")
        device_config = await session.scalar(
            select(ThresholdConfig)
            .where(
                ThresholdConfig.scope_key == str(device_id),
                ThresholdConfig.is_active.is_(True),
            )
            .order_by(ThresholdConfig.config_version.desc())
        )
        config = (
            device_config
            if device_config is not None and not device_config.is_removal
            else await session.scalar(
                select(ThresholdConfig)
                .where(
                    ThresholdConfig.scope_key == "GLOBAL",
                    ThresholdConfig.is_active.is_(True),
                )
                .order_by(ThresholdConfig.config_version.desc())
            )
        )
        control = await session.get(GlobalControl, "global-stop")
        if (
            config is None
            or config.config_version != payload.threshold_version
            or (control is not None and control.enabled)
        ):
            raise TaskUnavailable("task unavailable")

    def _unsigned(
        self,
        *,
        task_id: UUID,
        device_id: UUID,
        period_id: str,
        revision: int,
        action: TaskAction,
        issued_at,
        expires_at,
        payload: dict,
    ) -> dict:
        return {
            "task_id": str(task_id),
            "device_id": str(device_id),
            "period_id": period_id,
            "revision": revision,
            "action": action.value,
            "issued_at": utc_rfc3339(issued_at),
            "expires_at": utc_rfc3339(expires_at),
            "signing_key_version": self._signer.key_version,
            "payload": payload,
        }
