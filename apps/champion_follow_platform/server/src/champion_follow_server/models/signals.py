from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CHAR,
    DateTime,
    Integer,
    Numeric,
    SmallInteger,
    String,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from champion_follow_server.db.base import Base


READ_ONLY_PLAN01 = {"schema_owner": "plan01", "read_only": True}


class AnonymousActor(Base):
    __tablename__ = "anonymous_actors"
    __table_args__ = {"info": READ_ONLY_PLAN01}

    namespace_id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_key: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    display_no: Mapped[int] = mapped_column(BigInteger, unique=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AsOfCandidate(Base):
    __tablename__ = "asof_candidates"
    __table_args__ = {"info": READ_ONLY_PLAN01}

    id: Mapped[UUID] = mapped_column(primary_key=True)
    namespace_id: Mapped[UUID]
    snapshot_id: Mapped[UUID]
    issue: Mapped[str] = mapped_column(String(16))
    market: Mapped[str] = mapped_column(String(32))
    actor_key: Mapped[str] = mapped_column(CHAR(64))
    direction: Mapped[str] = mapped_column(String(4))
    signal_source_ms: Mapped[int] = mapped_column(BigInteger)
    lead_ms: Mapped[int] = mapped_column(BigInteger)
    prior_lead_times_ms: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    profile_level: Mapped[str] = mapped_column(String(16))
    profile_sample_count: Mapped[int] = mapped_column(BigInteger)
    profile_wins: Mapped[int] = mapped_column(BigInteger)
    profile_losses: Mapped[int] = mapped_column(BigInteger)
    profile_raw_win_rate: Mapped[Decimal] = mapped_column(Numeric(18, 12))
    profile_conservative_win_rate: Mapped[Decimal] = mapped_column(
        Numeric(18, 12)
    )
    profile_conservative_unit_return: Mapped[Decimal] = mapped_column(
        Numeric(18, 12)
    )
    base_rank: Mapped[int] = mapped_column(Integer)
    statistics_version: Mapped[str] = mapped_column(String(64))
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    unit_profit_micros: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
