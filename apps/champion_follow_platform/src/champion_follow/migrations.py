import hashlib
import re
from dataclasses import dataclass
from importlib.resources import files

from psycopg_pool import AsyncConnectionPool


MIGRATION_NAME = re.compile(r"^(?P<number>[0-9]{4})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True, slots=True)
class _Migration:
    version: str
    sql: str
    sha256: str


def _packaged_migrations() -> tuple[_Migration, ...]:
    directory = files("champion_follow").joinpath("sql")
    resources = sorted(
        (item for item in directory.iterdir() if item.name.endswith(".sql")),
        key=lambda item: item.name,
    )
    if not resources or resources[0].name != "0001_core.sql":
        raise RuntimeError("core migration is missing")
    migrations = []
    for expected_number, item in enumerate(resources, 1):
        match = MIGRATION_NAME.fullmatch(item.name)
        if match is None or int(match.group("number")) != expected_number:
            raise RuntimeError("migration versions must be contiguous and monotonic")
        payload = item.read_bytes()
        try:
            sql = payload.decode("utf-8")
        except UnicodeDecodeError:
            raise RuntimeError("migration is not valid UTF-8") from None
        migrations.append(_Migration(
            version=item.name.removesuffix(".sql"),
            sql=sql,
            sha256=hashlib.sha256(payload).hexdigest(),
        ))
    return tuple(migrations)


async def migrate(pool: AsyncConnectionPool) -> None:
    migrations = _packaged_migrations()
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(7260727)")
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version VARCHAR(64) PRIMARY KEY,sha256 CHAR(64) NOT NULL,"
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            result = await connection.execute(
                "SELECT version,sha256 FROM schema_migrations ORDER BY version"
            )
            applied_rows = tuple(await result.fetchall())
            applied = {row["version"]: row["sha256"] for row in applied_rows}
            packaged_versions = tuple(migration.version for migration in migrations)
            applied_versions = tuple(row["version"] for row in applied_rows)
            if set(applied_versions) - set(packaged_versions):
                raise RuntimeError("applied migration resource is missing")
            if applied_versions != packaged_versions[:len(applied_versions)]:
                raise RuntimeError("applied migrations are not a strict prefix")
            for migration in migrations:
                digest = applied.get(migration.version)
                if digest is not None:
                    if digest != migration.sha256:
                        raise RuntimeError("applied migration digest changed")
                    continue
                await connection.execute(migration.sql)
                await connection.execute(
                    "INSERT INTO schema_migrations(version,sha256) VALUES (%s,%s)",
                    (migration.version, migration.sha256),
                )
