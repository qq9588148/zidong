from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING

import pytest
from pydantic import ValidationError

from champion_follow.contracts.thresholds import (
    ThresholdPreviewRequest,
    ThresholdProposal,
)
from champion_follow.services.threshold_preview import ThresholdPreviewService


def test_effective_win_rate_uses_the_stricter_equivalent_threshold():
    proposal = ThresholdProposal(
        minimum_level="formal",
        minimum_conservative_win_rate=Decimal("0.52"),
        minimum_conservative_unit_return=Decimal("0.04"),
        minimum_followable_rate=Decimal("0.70"),
    )

    assert proposal.effective_minimum_win_rate == (
        (Decimal("1.04") / Decimal("1.96")).quantize(
            Decimal("0.000000000001"),
            rounding=ROUND_CEILING,
        )
    )


def test_effective_win_rate_keeps_the_direct_threshold_when_it_is_stricter():
    proposal = ThresholdProposal(
        minimum_level="candidate",
        minimum_conservative_win_rate=Decimal("0.60"),
        minimum_conservative_unit_return=Decimal("0.04"),
        minimum_followable_rate=Decimal("0.50"),
    )

    assert proposal.effective_minimum_win_rate == Decimal("0.600000000000")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("minimum_conservative_win_rate", Decimal("1.01")),
        ("minimum_conservative_unit_return", Decimal("0.97")),
        ("minimum_followable_rate", Decimal("-0.01")),
    ),
)
def test_threshold_controls_reject_out_of_range_values(field, value):
    values = {
        "minimum_level": "formal",
        "minimum_conservative_win_rate": Decimal("0.52"),
        "minimum_conservative_unit_return": Decimal("0.04"),
        "minimum_followable_rate": Decimal("0.70"),
    }
    values[field] = value

    with pytest.raises(ValidationError):
        ThresholdProposal(**values)


def test_threshold_controls_reject_more_than_twelve_decimal_places():
    with pytest.raises(ValidationError):
        ThresholdProposal(
            minimum_level="formal",
            minimum_conservative_win_rate=Decimal("0.5200000000001"),
            minimum_conservative_unit_return=Decimal("0.04"),
            minimum_followable_rate=Decimal("0.70"),
        )


@pytest.mark.parametrize("safe_lead_ms", (True, 2**63))
def test_preview_request_rejects_non_bigint_safe_lead(safe_lead_ms):
    with pytest.raises(ValidationError):
        ThresholdPreviewRequest(
            minimum_level="formal",
            minimum_conservative_win_rate=Decimal("0.52"),
            minimum_conservative_unit_return=Decimal("0.04"),
            minimum_followable_rate=Decimal("0.70"),
            as_of=datetime(2026, 7, 27, tzinfo=timezone.utc),
            safe_lead_ms=safe_lead_ms,
        )


@pytest.mark.asyncio
async def test_preview_service_rejects_safe_lead_outside_bigint_before_database_use():
    proposal = ThresholdProposal(
        minimum_level="formal",
        minimum_conservative_win_rate=Decimal("0.52"),
        minimum_conservative_unit_return=Decimal("0.04"),
        minimum_followable_rate=Decimal("0.70"),
    )

    with pytest.raises(ValueError, match="safe_lead_ms_invalid"):
        await ThresholdPreviewService(None).preview(
            proposal=proposal,
            as_of=datetime(2026, 7, 27, tzinfo=timezone.utc),
            safe_lead_ms=2**63,
        )
