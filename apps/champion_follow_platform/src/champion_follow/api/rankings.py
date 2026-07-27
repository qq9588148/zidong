from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from champion_follow.contracts.rankings import RankingResponse
from champion_follow.services.rankings import RankingNotFound


router = APIRouter(prefix="/v1/rankings", tags=["rankings"])


@router.get("/{market}", response_model=RankingResponse)
async def get_ranking(
    market: str,
    request: Request,
    as_of_issue: Annotated[
        str | None,
        Query(pattern=r"^[0-9]{8,16}$"),
    ] = None,
) -> RankingResponse:
    try:
        return await request.app.state.rankings.get(
            market,
            as_of_issue=as_of_issue,
        )
    except RankingNotFound:
        raise HTTPException(
            404,
            detail={"code": "ranking_not_found"},
        ) from None
