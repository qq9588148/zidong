from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def create_pool(database_url: str) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=8,
        open=False,
        kwargs={"row_factory": dict_row},
    )


@asynccontextmanager
async def open_pool(database_url: str):
    pool = create_pool(database_url)
    try:
        await pool.open(wait=True)
        yield pool
    finally:
        await pool.close()
