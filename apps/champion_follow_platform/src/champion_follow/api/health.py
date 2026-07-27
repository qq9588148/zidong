from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    async with request.app.state.db.connection() as connection:
        value = await connection.execute("SELECT 1 AS alive")
        row = await value.fetchone()
    return {"status": "ok", "database": "ok" if row["alive"] == 1 else "error"}
