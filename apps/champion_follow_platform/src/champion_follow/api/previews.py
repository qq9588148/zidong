from fastapi import APIRouter, HTTPException, Request

from champion_follow.contracts.thresholds import (
    ThresholdPreviewRequest,
    ThresholdPreviewResponse,
)
from champion_follow.repositories.thresholds import (
    PreviewStateError,
    WatermarkUnavailable,
)


router = APIRouter(prefix="/v1/threshold-previews", tags=["thresholds"])


@router.post("", response_model=ThresholdPreviewResponse)
async def create_threshold_preview(
    payload: ThresholdPreviewRequest,
    request: Request,
) -> ThresholdPreviewResponse:
    try:
        return await request.app.state.threshold_previews.preview(
            proposal=payload,
            device_id=payload.device_id,
            as_of=payload.as_of,
            safe_lead_ms=payload.safe_lead_ms,
            safe_lead_version=payload.safe_lead_version,
        )
    except WatermarkUnavailable:
        raise HTTPException(
            409,
            detail={"code": "watermark_unavailable"},
        ) from None
    except PreviewStateError:
        raise HTTPException(
            409,
            detail={"code": "preview_state_invalid"},
        ) from None
