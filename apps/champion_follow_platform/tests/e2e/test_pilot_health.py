from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from urllib.parse import quote

import httpx
import psycopg
import pytest


ROOT = Path(__file__).resolve().parents[2]
CHECK_SCHEMA_PATH = ROOT / "ops" / "migrations" / "check_schema.py"


def _schema_module():
    spec = importlib.util.spec_from_file_location(
        "champion_pilot_check_schema", CHECK_SCHEMA_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("schema_check_module_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pilot_environment() -> dict[str, str]:
    path = Path(
        os.environ.get("PILOT_ENV_FILE", ROOT / "ops" / "run" / "pilot.env")
    )
    if not path.is_file():
        raise RuntimeError("pilot_env_missing")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.isidentifier() or key in values:
            raise RuntimeError("pilot_env_invalid")
        values[key] = value
    return values


class DatabaseProbe:
    def __init__(self, connection):
        self.connection = connection

    def scalar(self, statement: str):
        with self.connection.cursor() as cursor:
            cursor.execute(statement)
            row = cursor.fetchone()
        return None if row is None else row[0]


@pytest.fixture(scope="module")
def pilot_env():
    return _pilot_environment()


@pytest.fixture(scope="module")
def http(pilot_env):
    port = int(pilot_env.get("PILOT_SERVER_PORT", "58000"))
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
        yield client


@pytest.fixture(scope="module")
def db(pilot_env):
    port = int(pilot_env.get("PILOT_POSTGRES_PORT", "55440"))
    user = quote(pilot_env["POSTGRES_USER"], safe="")
    password = quote(pilot_env["POSTGRES_PASSWORD"], safe="")
    database = quote(pilot_env["POSTGRES_DB"], safe="")
    url = f"postgresql://{user}:{password}@127.0.0.1:{port}/{database}"
    with psycopg.connect(url) as connection:
        yield DatabaseProbe(connection)


@pytest.mark.e2e
def test_pilot_starts_with_empty_database_and_no_executable_threshold(http, db):
    assert http.get("/healthz").json() == {"status": "ok", "database": "ok"}
    assert db.scalar("select count(*) from threshold_configs") == 0
    assert db.scalar("select count(*) from device_task_revisions") == 0


@pytest.mark.e2e
def test_schema_matches_declared_head(db):
    schema = _schema_module()
    expected_core = schema.expected_core_migrations()
    assert db.scalar(
        "select sha256 from schema_migrations where version='0001_core'"
    ) == expected_core["0001_core"]
    assert db.scalar("select version_num from alembic_version") == (
        schema.expected_alembic_head()
    )
