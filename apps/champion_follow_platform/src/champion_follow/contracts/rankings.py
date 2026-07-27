from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


ActorRef = Annotated[str, Field(pattern=r"^A[0-9]{6,12}$")]


class RankingEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_ref: ActorRef
    market: str
    rank: int
    level: Literal["observed", "candidate", "formal", "core"]
    sample_count: int
    raw_win_rate: Decimal
    conservative_win_rate: Decimal
    unit_return: Decimal
    conservative_unit_return: Decimal
    blind_count: int
    blind_unit_return: Decimal


class RankingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    market: str
    issue: str
    frozen_at: AwareDatetime
    statistics_version: str
    entries: tuple[RankingEntryResponse, ...]
