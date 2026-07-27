from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass(frozen=True, slots=True)
class AuthDatabase:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]


@asynccontextmanager
async def open_auth_engine(database_url: str):
    engine = create_async_engine(database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield AuthDatabase(engine=engine, session_factory=sessions)
    finally:
        await engine.dispose()


async def get_session(request: Request):
    async with request.app.state.auth_sessions() as session:
        yield session
