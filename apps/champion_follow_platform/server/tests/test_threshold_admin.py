from decimal import Decimal
from uuid import UUID

import pytest
import pytest_asyncio

from champion_follow.contracts.thresholds import (
    PreviewWindow,
    ThresholdPreviewResult,
)
from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AccountStatus,
    Device,
    DeviceStatus,
)
from champion_follow_server.services.thresholds import (
    PreviewMismatch,
    ThresholdProposal,
    ThresholdService,
    effective_min_win_rate,
)


class FrozenPreviewSource:
    def __init__(self, clock) -> None:
        self.clock = clock
        self.calls = []

    async def preview(self, **kwargs):
        self.calls.append(kwargs)
        return ThresholdPreviewResult(
            preview_id=UUID("00000000-0000-0000-0000-000000000101"),
            watermark_snapshot_id=UUID(
                "00000000-0000-0000-0000-000000000102"
            ),
            generated_at=self.clock.now(),
            windows=(
                PreviewWindow(
                    days=7,
                    frozen_signal_count=10,
                    executable_signal_count=7,
                    win_count=5,
                    loss_count=2,
                    unit_profit_micros=2_800_000,
                    raw_win_rate=Decimal("0.714285714286"),
                    conservative_win_rate=Decimal("0.358934451832"),
                ),
                PreviewWindow(
                    days=30,
                    frozen_signal_count=40,
                    executable_signal_count=30,
                    win_count=20,
                    loss_count=10,
                    unit_profit_micros=9_200_000,
                    raw_win_rate=Decimal("0.666666666667"),
                    conservative_win_rate=Decimal("0.487954991637"),
                ),
            ),
        )


@pytest.fixture
def frozen_preview_source(clock):
    return FrozenPreviewSource(clock)


@pytest.fixture
def threshold_service(frozen_preview_source, audit_writer, clock):
    return ThresholdService(frozen_preview_source, audit_writer, clock)


@pytest.fixture
def proposal() -> ThresholdProposal:
    return ThresholdProposal(
        minimum_level="FORMAL",
        minimum_conservative_win_rate=Decimal("0.5200000000"),
        minimum_conservative_roi=Decimal("0.0192000000"),
        minimum_followable_rate=Decimal("0.8000000000"),
    )


@pytest_asyncio.fixture
async def threshold_device(db_session):
    account = Account(
        username_canonical="threshold-user",
        password_hash="test-hash",
        role=AccountRole.USER,
        status=AccountStatus.ACTIVE,
        admin_slot=None,
    )
    db_session.add(account)
    await db_session.flush()
    device = Device(
        account_id=account.id,
        public_key_spki_der=b"test-public-key",
        public_key_fingerprint=b"t" * 32,
        binding_epoch=1,
        status=DeviceStatus.ACTIVE,
    )
    db_session.add(device)
    await db_session.flush()
    return device


def test_effective_minimum_uses_stricter_equivalent_condition() -> None:
    assert effective_min_win_rate(
        Decimal("0.5200000000"), Decimal("0.0500000000")
    ) == Decimal("0.5357142858")


@pytest.mark.asyncio
async def test_activation_requires_unexpired_matching_preview(
    db_session,
    admin_account,
    threshold_service,
    frozen_preview_source,
    clock,
    proposal,
) -> None:
    preview = await threshold_service.preview(
        db_session,
        actor=admin_account,
        proposal=proposal,
        device_id=None,
        now=clock.now(),
    )
    changed = proposal.model_copy(
        update={"minimum_followable_rate": Decimal("0.7000000000")}
    )

    with pytest.raises(PreviewMismatch):
        await threshold_service.activate(
            db_session,
            actor=admin_account,
            proposal=changed,
            device_id=None,
            preview_id=preview.id,
            reason="activate global threshold",
            request_id="request-1",
            now=clock.now(),
        )
    assert frozen_preview_source.calls[0]["proposal"].minimum_level == "formal"


@pytest.mark.asyncio
async def test_global_and_device_override_effective_config(
    db_session,
    admin_account,
    threshold_service,
    threshold_device,
    clock,
    proposal,
) -> None:
    device = threshold_device
    assert await threshold_service.get_effective(db_session, device.id) is None
    global_preview = await threshold_service.preview(
        db_session,
        actor=admin_account,
        proposal=proposal,
        device_id=None,
        now=clock.now(),
    )
    global_config = await threshold_service.activate(
        db_session,
        actor=admin_account,
        proposal=proposal,
        device_id=None,
        preview_id=global_preview.id,
        reason="activate global threshold",
        request_id="request-global",
        now=clock.now(),
    )
    assert (
        await threshold_service.get_effective(db_session, device.id)
    ).id == global_config.id

    override = proposal.model_copy(
        update={"minimum_followable_rate": Decimal("0.9000000000")}
    )
    device_preview = await threshold_service.preview(
        db_session,
        actor=admin_account,
        proposal=override,
        device_id=device.id,
        now=clock.now(),
    )
    device_config = await threshold_service.activate(
        db_session,
        actor=admin_account,
        proposal=override,
        device_id=device.id,
        preview_id=device_preview.id,
        reason="activate device override",
        request_id="request-device",
        now=clock.now(),
    )
    assert (
        await threshold_service.get_effective(db_session, device.id)
    ).id == device_config.id

    await threshold_service.remove_override(
        db_session,
        actor=admin_account,
        device_id=device.id,
        reason="return to global threshold",
        request_id="request-remove",
        now=clock.now(),
    )
    assert (
        await threshold_service.get_effective(db_session, device.id)
    ).id == global_config.id
