import pytest

from champion_follow import db


@pytest.mark.asyncio
async def test_open_pool_closes_pool_when_open_fails(monkeypatch):
    class FailingPool:
        closed = False

        async def open(self, *, wait):
            raise RuntimeError("open failed")

        async def close(self):
            self.closed = True

    pool = FailingPool()
    monkeypatch.setattr(db, "create_pool", lambda database_url: pool)

    with pytest.raises(RuntimeError, match="open failed"):
        async with db.open_pool("placeholder"):
            pass

    assert pool.closed
