# Champion Follow 03: Authorization, Signed Tasks, and Admin Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the central platform's one-time authorization, one-account/one-device binding, sole-admin TOTP authentication, versioned signal thresholds, signed monotonic `BET`/`CANCEL` task channel, administrator reports, and immutable audit trail.

**Architecture:** A FastAPI service owns all authorization and administrative state in PostgreSQL. Device enrollment proves possession of a Windows-CNG-backed ECDSA P-256 key, opaque sessions remain independently revocable, threshold activation requires an `as-of` preview, and every device task is committed before a signed revision is broadcast over the device's authenticated WebSocket. The administrator uses a same-origin static console with an HttpOnly refresh cookie; ordinary clients receive only their own tasks and reports.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async, PostgreSQL 16, Alembic, Argon2id, PyOTP, `cryptography` ECDSA-P256/Ed25519/AES-GCM, vanilla HTML/CSS/JavaScript, pytest, pytest-asyncio, HTTPX, Docker Compose.

---

## Scope and prerequisite contracts

This is implementation plan **03**. Run it only after plans 01 and 02 pass. The repository boundaries are:

- Plan 01 owns the core package at `apps/champion_follow_platform/src/champion_follow/`, the authoritative PostgreSQL core tables, frozen `asof_candidates`, ranking snapshots, and `ThresholdPreviewService`;
- Plan 02 owns `apps/champion_follow_platform/collector/` and the `/v1/collector/session`, `/events`, and `/heartbeat` wire contract;
- this plan creates `apps/champion_follow_platform/server/` as the auth/admin/API packaging boundary, but it runs in the **same FastAPI process** as Plan 01 rather than introducing a second service;
- `champion_follow_server.app.create_app()` starts Plan 01's psycopg pool, its own SQLAlchemy async session factory against the same database, registers both sets of routers, and shuts both down in one lifespan;
- Plan 01 migrations remain authoritative for existing core tables. This plan's Alembic revision adds only auth, device, task, assignment, order, settlement, balance, telemetry and audit tables, plus read-only SQLAlchemy mappings for the core snapshot rows it consumes.

The Plan 01 preview boundary used here is:

```python
from champion_follow.services.threshold_preview import (
    ThresholdPreviewResult,
    ThresholdPreviewService,
)

result = await ThresholdPreviewService(core_pool).preview(
    proposal=proposal,
    device_id=device_id,
    as_of=as_of,
)
```

`ThresholdPreviewResult` contains a frozen `watermark_snapshot_id`, `generated_at`, and exactly the 7-day and 30-day windows defined in Plan 01. If an import name changes while executing Plan 01, adapt only this thin boundary and keep the same causal data contract; do not duplicate preview calculations in the auth package.

## File structure

Create or modify only these implementation areas while executing this plan:

```text
apps/champion_follow_platform/server/
├── pyproject.toml                                      # pinned auth, crypto, QR and test dependencies
├── alembic.ini                                        # URL-free Alembic configuration
├── alembic/env.py                                     # async runner excluding every Plan 01-owned table
├── alembic/versions/0003_auth_admin_tasks.py          # complete auth/task/assignment/ledger schema and audit trigger
├── src/champion_follow_server/
│   ├── app.py                                         # one process: core pool + auth session + all routers
│   ├── db/base.py                                     # auth/admin SQLAlchemy declarative base
│   ├── db/session.py                                  # async session factory for the shared PostgreSQL DB
│   ├── config.py                                      # non-secret key paths, lifetimes, trusted origin
│   ├── cli/admin.py                                   # sole-admin bootstrap with local QR output
│   ├── models/auth.py                                 # accounts, devices, codes, challenges, sessions, TOTP
│   ├── models/signals.py                              # read-only mappings of frozen core candidates/signals
│   ├── models/assignments.py                          # immutable per-period device allocation rows
│   ├── models/ledger.py                               # device orders, settlements, balances and bankroll telemetry
│   ├── models/admin.py                                # threshold previews/configs, global control, audit
│   ├── models/device_tasks.py                         # append-only revisions and current heads
│   ├── schemas/auth.py                                # registration/login/session request and response models
│   ├── schemas/admin.py                               # threshold, report and audit DTOs
│   ├── schemas/device_tasks.py                        # canonical BET/CANCEL envelopes
│   ├── schemas/device_events.py                       # strict signed client-event DTOs
│   ├── security/passwords.py                          # Argon2id hashing
│   ├── security/secrets.py                            # opaque token/code digests and AES-GCM secret vault
│   ├── security/totp.py                               # TOTP enrollment and bounded verification
│   ├── security/device_keys.py                        # ECDSA P-256 SPKI parsing and proof verification
│   ├── security/task_signing.py                       # canonical JSON and server Ed25519 signatures
│   ├── services/audit.py                              # redacted append-only audit writes
│   ├── services/admin_bootstrap.py                    # one-time sole-admin TOTP initialization
│   ├── services/authorization_codes.py                # single-use REGISTER/REBIND codes
│   ├── services/device_binding.py                     # transactional account/device enrollment and rebind
│   ├── services/sessions.py                           # opaque access/refresh issue, rotate and revoke
│   ├── services/thresholds.py                         # preview-backed version activation and effective config
│   ├── services/device_task_revisions.py              # row-locked monotonic BET/CANCEL persistence
│   ├── services/device_allocator.py                   # deterministic champion allocation and pair caps
│   ├── services/device_ledger.py                      # idempotent orders, settlement and telemetry projections
│   ├── services/task_hub.py                           # per-device in-process notification queues
│   ├── services/reports.py                            # Shanghai-period balances, turnover and P/L
│   ├── api/dependencies.py                            # session, user, device and sole-admin guards
│   ├── api/auth.py                                    # enrollment, login, refresh and logout routes
│   ├── api/admin.py                                   # admin control/report/audit routes
│   ├── api/device_events.py                            # signed client event ingestion and sync
│   └── api/device_ws.py                               # authenticated task WebSocket and reconnect sync
└── static/admin/
    ├── index.html                                     # login, overview, users, thresholds, codes, audit
    ├── app.js                                         # same-origin API client; access token only in memory
    └── style.css                                      # responsive desktop/mobile-read-only layout

apps/champion_follow_platform/contracts/
└── device-task-v1.schema.json                         # shared signed BET/CANCEL wire contract

apps/champion_follow_platform/server/tests/
├── factories/auth.py                                  # deterministic fake identities and keys
├── test_security_settings.py
├── test_auth_models.py
├── test_passwords_and_secrets.py
├── test_task_signing.py
├── test_audit.py
├── test_admin_totp.py
├── test_authorization_codes.py
├── test_device_binding.py
├── test_sessions_api.py
├── test_threshold_admin.py
├── test_device_task_revisions.py
├── test_device_task_websocket.py
├── test_admin_reports.py
├── test_admin_api.py
├── test_admin_static.py
├── test_security_privacy_scan.py
└── test_auth_admin_e2e.py
```

## API and security invariants

- PostgreSQL stores only Argon2 password hashes and HMAC-SHA256 digests of authorization/access/refresh tokens.
- Only one row may ever occupy the `ADMIN` slot. TOTP is mandatory for that account.
- A device enrollment/rebind succeeds only after an unexpired one-time code and a valid ECDSA P-256 proof are consumed in one transaction.
- Refresh sessions are bound to account, device and binding epoch; unbind, disable and global revocation invalidate them immediately.
- Admin browser refresh state is an HttpOnly, Secure, SameSite=Strict cookie. Access tokens exist only in JavaScript memory. State-changing admin calls require a matching CSRF header and trusted `Origin`.
- Device task signatures cover every executable field. For a `(device_id, period_id)` pair, only the highest committed revision is current.
- `CANCEL` is a signed tombstone. A late or out-of-order lower `BET` can never become current again.
- Passwords, TOTP seeds, authorization code plaintext, access/refresh token plaintext, server signing private keys, platform credentials, cookies, raw third-party IDs and private request bodies never enter API responses, audit records, application logs, fixtures or snapshots.

---

### Task 1: Pin security dependencies and settings

**Files:**
- Create: `apps/champion_follow_platform/server/pyproject.toml`
- Create: `apps/champion_follow_platform/server/Dockerfile`
- Create: `apps/champion_follow_platform/server/alembic.ini`
- Create: `apps/champion_follow_platform/server/alembic/env.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/config.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/db/base.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/db/session.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/app.py`
- Modify: `apps/champion_follow_platform/src/champion_follow/main.py`
- Modify: `apps/champion_follow_platform/compose.yaml`
- Test: `apps/champion_follow_platform/server/tests/test_security_settings.py`
- Test: `apps/champion_follow_platform/server/tests/test_combined_app.py`

- [ ] **Step 1: Write the failing settings test**

```python
# tests/test_security_settings.py
from pathlib import Path

import pytest
from pydantic import ValidationError

from champion_follow_server.config import Settings


def test_security_settings_require_key_files_and_https_origin(tmp_path: Path) -> None:
    signing = tmp_path / "task-signing.pem"
    vault = tmp_path / "vault.key"
    allocation = tmp_path / "allocation-seed.key"
    signing.write_text("not-a-real-key", encoding="utf-8")
    vault.write_bytes(b"0" * 32)
    allocation.write_bytes(b"a" * 32)

    settings = Settings(
        database_url="postgresql+asyncpg://app:app@postgres/app",
        public_base_url="https://console.example.test",
        trusted_admin_origin="https://console.example.test",
        task_signing_key_path=signing,
        secret_vault_key_path=vault,
        allocation_seed_path=allocation,
        token_pepper="test-only-token-pepper-with-32-bytes",
    )

    assert settings.access_token_ttl_seconds == 900
    assert settings.refresh_token_ttl_seconds == 2_592_000
    assert settings.enrollment_challenge_ttl_seconds == 300
    assert settings.allocation_seed_version == "allocation-v1"


def test_http_admin_origin_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+asyncpg://app:app@postgres/app",
            public_base_url="https://console.example.test",
            trusted_admin_origin="http://console.example.test",
            task_signing_key_path=Path("/run/secrets/task-signing.pem"),
            secret_vault_key_path=Path("/run/secrets/vault.key"),
            allocation_seed_path=Path("/run/secrets/allocation-seed.key"),
            token_pepper="test-only-token-pepper-with-32-bytes",
        )
```

- [ ] **Step 2: Run the test and verify the missing fields fail**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_security_settings.py -q
```

Expected: FAIL during `Settings` construction because the security fields do not exist.

- [ ] **Step 3: Add pinned dependencies**

Add these exact entries to the server dependency lists, preserving dependencies from plans 01 and 02:

```toml
dependencies = [
  "alembic==1.16.4",
  "argon2-cffi==25.1.0",
  "asyncpg==0.30.0",
  "cryptography==45.0.5",
  "fastapi==0.116.1",
  "pydantic==2.11.7",
  "pydantic-settings==2.10.1",
  "pyotp==2.9.0",
  "qrcode[pil]==8.2",
  "sqlalchemy[asyncio]==2.0.41",
]

[project.optional-dependencies]
test = [
  "httpx==0.28.1",
  "jsonschema==4.25.0",
  "pytest==8.4.1",
  "pytest-asyncio==1.0.0",
]

[project.scripts]
champion-admin = "champion_follow_server.cli.admin:main"
```

- [ ] **Step 4: Add validated security settings**

```python
# src/champion_follow_server/config.py (merge these fields into the existing Settings)
from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHAMPION_", case_sensitive=False)

    database_url: str
    public_base_url: AnyHttpUrl
    trusted_admin_origin: AnyHttpUrl
    task_signing_key_path: Path
    task_signing_key_version: str = Field(default="task-v1", pattern=r"^[a-z0-9-]{1,32}$")
    secret_vault_key_path: Path
    allocation_seed_path: Path
    allocation_seed_version: str = Field(default="allocation-v1", pattern=r"^[a-z0-9-]{1,32}$")
    token_pepper: SecretStr = Field(min_length=32)
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_seconds: int = Field(default=2_592_000, ge=86_400)
    enrollment_challenge_ttl_seconds: int = Field(default=300, ge=60, le=900)
    authorization_code_ttl_seconds: int = Field(default=86_400, ge=300)
    threshold_preview_ttl_seconds: int = Field(default=1800, ge=300, le=3600)

    @field_validator("public_base_url", "trusted_admin_origin")
    @classmethod
    def require_https_origin(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("public origins must use https")
        return value
```

- [ ] **Step 5: Create the shared-database API wrapper**

`db/base.py` defines `Base`, `UtcTimestampMixin`, and UUID defaults. `db/session.py` creates an `async_sessionmaker` from `Settings.database_url` and exposes `get_session()`. Production startup never calls `Base.metadata.create_all()`; Alembic `0003_auth_admin_tasks.py` owns the server tables and Plan 01 owns the mapped core tables. `app.py` must compose, not proxy, the core app:

Create `alembic.ini` without a database URL:

```ini
[alembic]
script_location = %(here)s/alembic
prepend_sys_path = src
sqlalchemy.url =
```

`alembic/env.py` imports `champion_follow_server.models` so Task 2 mappings are registered, sets `target_metadata = Base.metadata`, loads `Settings().database_url` only in memory, and runs online migrations with `async_engine_from_config(..., poolclass=NullPool)` plus `await connection.run_sync(do_run_migrations)`. Its `context.configure(...)` must pass this ownership filter so autogenerate can neither create nor delete any Plan 01 table, including the two later read-only ORM mappings:

```python
PLAN01_OWNED_TABLES = frozenset({
    "actor_profiles", "anonymous_actors", "asof_candidates", "capture_gaps",
    "collector_event_receipts", "collector_heartbeats", "collectors", "game_issues",
    "identity_namespaces", "import_batches", "issue_evaluations", "prediction_samples",
    "processing_state", "ranking_entries", "ranking_snapshots", "schema_migrations",
    "source_events", "threshold_preview_windows", "threshold_previews",
})


def include_object(obj, name, type_, reflected, compare_to):
    table = obj if type_ == "table" else getattr(obj, "table", None)
    table_name = name if type_ == "table" else getattr(table, "name", None)
    mapped_info = getattr(compare_to, "info", {}) if compare_to is not None else {}
    if table_name in PLAN01_OWNED_TABLES:
        return False
    if getattr(table, "info", {}).get("schema_owner") == "plan01":
        return False
    if mapped_info.get("schema_owner") == "plan01":
        return False
    return True


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
```

Disable offline mode because it risks serializing a credential-bearing URL; pilot and CI always migrate a disposable PostgreSQL database. Neither Alembic config nor logs may contain the resolved URL. `admin_threshold_previews` is intentionally absent from `PLAN01_OWNED_TABLES`, while Plan 01's `threshold_previews`, `anonymous_actors`, and `asof_candidates` are always excluded.

```python
from contextlib import asynccontextmanager

from champion_follow.db import open_pool
from champion_follow.main import register_core_routers
from fastapi import FastAPI

from .config import Settings
from .db.session import open_auth_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        core_url = resolved.database_url.replace("postgresql+asyncpg://", "postgresql://")
        async with open_pool(core_url) as core_pool, open_auth_engine(resolved.database_url) as auth:
            app.state.db = core_pool
            app.state.core_pool = core_pool
            app.state.auth_sessions = auth.session_factory
            yield

    app = FastAPI(title="Champion Follow Platform", lifespan=lifespan)
    app.state.settings = resolved
    register_core_routers(app)
    return app
```

Add `register_core_routers(app)` to Plan 01's `champion_follow.main` without creating a second lifespan. The server `Dockerfile` installs the root core package and then `server/`; the root Compose `server` service runs one Uvicorn process and depends on healthy PostgreSQL. `tests/test_combined_app.py` asserts `/healthz`, `/v1/rankings/...`, and the auth route table coexist on one `FastAPI` instance and share one database.

- [ ] **Step 6: Run settings and composition tests**

```bash
docker compose -f apps/champion_follow_platform/compose.yaml build server
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_security_settings.py tests/test_combined_app.py -q
```

Expected: `3 passed`; exactly one Uvicorn application is created.

- [ ] **Step 7: Commit the dependency, settings, and composition boundary**

```bash
git add apps/champion_follow_platform/compose.yaml \
  apps/champion_follow_platform/server/pyproject.toml \
  apps/champion_follow_platform/server/Dockerfile \
  apps/champion_follow_platform/server/alembic.ini \
  apps/champion_follow_platform/server/alembic/env.py \
  apps/champion_follow_platform/server/src/champion_follow_server/config.py \
  apps/champion_follow_platform/server/src/champion_follow_server/db \
  apps/champion_follow_platform/server/src/champion_follow_server/app.py \
  apps/champion_follow_platform/src/champion_follow/main.py \
  apps/champion_follow_platform/server/tests/test_security_settings.py \
  apps/champion_follow_platform/server/tests/test_combined_app.py
git commit -m "build: compose champion core and api server"
```


---

### Task 2: Add auth, threshold, audit, task, assignment, and ledger tables

**Files:**
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/models/__init__.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/models/auth.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/models/admin.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/models/device_tasks.py`
- Create: `apps/champion_follow_platform/server/alembic/versions/0003_auth_admin_tasks.py`
- Test: `apps/champion_follow_platform/server/tests/test_auth_models.py`

- [ ] **Step 1: Write failing PostgreSQL constraint tests**

```python
# tests/test_auth_models.py
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from champion_follow_server.models.auth import (
    Account,
    AccountRole,
    AccountStatus,
    AuthorizationCode,
    CodePurpose,
)
from champion_follow_server.models.admin import ThresholdPreview


@pytest.mark.asyncio
async def test_only_one_admin_slot_can_exist(db_session) -> None:
    db_session.add_all(
        [
            Account(username_canonical="owner-a", password_hash="x", role=AccountRole.ADMIN,
                    status=AccountStatus.ACTIVE, admin_slot=1),
            Account(username_canonical="owner-b", password_hash="x", role=AccountRole.ADMIN,
                    status=AccountStatus.ACTIVE, admin_slot=1),
        ]
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_authorization_code_digest_is_unique(db_session) -> None:
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    first = AuthorizationCode(
        digest=b"a" * 32,
        purpose=CodePurpose.REGISTER,
        expires_at=expires_at,
    )
    second = AuthorizationCode(
        digest=b"a" * 32,
        purpose=CodePurpose.REGISTER,
        expires_at=expires_at,
    )
    db_session.add_all([first, second])
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_0003_already_contains_assignment_and_device_ledger_tables(async_engine) -> None:
    expected = {
        "admin_threshold_previews",
        "assignment_rounds",
        "device_assignments",
        "pair_sequence_counters",
        "device_event_cursors",
        "device_events",
        "orders",
        "settlements",
        "balance_snapshots",
        "bankroll_telemetry",
        "latency_samples",
    }
    async with async_engine.connect() as connection:
        actual = await connection.run_sync(lambda sync_connection: set(inspect(sync_connection).get_table_names()))
    assert expected <= actual


def test_admin_preview_persistence_does_not_shadow_plan01_table() -> None:
    assert ThresholdPreview.__tablename__ == "admin_threshold_previews"
```

- [ ] **Step 2: Run the test to verify model imports fail**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_auth_models.py -q
```

Expected: collection FAIL with `ModuleNotFoundError` for `models.auth`.

- [ ] **Step 3: Create the authentication models**

Implement these enums and tables in `models/auth.py`; use UUID primary keys from `new_uuid`, UTC-aware datetimes, and SQLAlchemy `LargeBinary` for digests and public keys:

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from champion_follow_server.db.base import Base, UtcTimestampMixin, new_uuid


class AccountRole(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


class AccountStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class CodePurpose(StrEnum):
    REGISTER = "REGISTER"
    REBIND = "REBIND"


class DeviceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    UNBOUND = "UNBOUND"


class SessionKind(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


class Account(UtcTimestampMixin, Base):
    __tablename__ = "app_accounts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    username_canonical: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[AccountRole] = mapped_column(Enum(AccountRole, native_enum=False))
    status: Mapped[AccountStatus] = mapped_column(Enum(AccountStatus, native_enum=False))
    admin_slot: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Device(UtcTimestampMixin, Base):
    __tablename__ = "devices"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("app_accounts.id", ondelete="CASCADE"), index=True)
    public_key_spki_der: Mapped[bytes] = mapped_column(LargeBinary)
    public_key_fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    binding_epoch: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[DeviceStatus] = mapped_column(Enum(DeviceStatus, native_enum=False))
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    account: Mapped[Account] = relationship()


class AuthorizationCode(UtcTimestampMixin, Base):
    __tablename__ = "authorization_codes"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    digest: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    purpose: Mapped[CodePurpose] = mapped_column(Enum(CodePurpose, native_enum=False))
    target_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_accounts.id", ondelete="CASCADE"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_by_device_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )


class EnrollmentChallenge(UtcTimestampMixin, Base):
    __tablename__ = "enrollment_challenges"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    authorization_code_id: Mapped[UUID] = mapped_column(
        ForeignKey("authorization_codes.id", ondelete="CASCADE"), index=True
    )
    nonce: Mapped[bytes] = mapped_column(LargeBinary(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(UtcTimestampMixin, Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (UniqueConstraint("access_digest"), UniqueConstraint("refresh_digest"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("app_accounts.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[SessionKind] = mapped_column(Enum(SessionKind, native_enum=False))
    binding_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    family_id: Mapped[UUID] = mapped_column(index=True, default=new_uuid)
    rotated_from_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="SET NULL"), nullable=True
    )
    access_digest: Mapped[bytes] = mapped_column(LargeBinary(32))
    refresh_digest: Mapped[bytes] = mapped_column(LargeBinary(32))
    csrf_digest: Mapped[bytes | None] = mapped_column(LargeBinary(32), nullable=True)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeviceLoginChallenge(UtcTimestampMixin, Base):
    __tablename__ = "device_login_challenges"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("app_accounts.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AdminTotp(UtcTimestampMixin, Base):
    __tablename__ = "admin_totp"
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Add a PostgreSQL partial unique index in the migration, not merely application logic:

```python
op.create_index(
    "uq_devices_one_active_per_account",
    "devices",
    ["account_id"],
    unique=True,
    postgresql_where=sa.text("status = 'ACTIVE'"),
)
```

- [ ] **Step 4: Create admin and task models**

Create `models/admin.py` with this complete shape. `scope_key` is `GLOBAL` or the canonical device UUID and lets PostgreSQL enforce one active row per scope:

```python
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, JSON, LargeBinary, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from champion_follow_server.db.base import Base, UtcTimestampMixin, new_uuid


class ThresholdScope(StrEnum):
    GLOBAL = "GLOBAL"
    DEVICE = "DEVICE"


class UserLevel(StrEnum):
    OBSERVER = "OBSERVER"
    CANDIDATE = "CANDIDATE"
    FORMAL = "FORMAL"
    CORE = "CORE"


class ThresholdPreview(UtcTimestampMixin, Base):
    __tablename__ = "admin_threshold_previews"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    created_by_account_id: Mapped[UUID] = mapped_column(ForeignKey("app_accounts.id"), index=True)
    device_id: Mapped[UUID | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    proposal_digest: Mapped[bytes] = mapped_column(LargeBinary(32))
    watermark_snapshot_id: Mapped[UUID] = mapped_column(index=True)
    windows: Mapped[dict] = mapped_column(JSON)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ThresholdConfig(UtcTimestampMixin, Base):
    __tablename__ = "threshold_configs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    config_version: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    scope: Mapped[ThresholdScope] = mapped_column(String(16))
    scope_key: Mapped[str] = mapped_column(String(64), index=True)
    device_id: Mapped[UUID | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    minimum_level: Mapped[UserLevel | None] = mapped_column(String(16), nullable=True)
    minimum_conservative_win_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 10), nullable=True)
    minimum_conservative_roi: Mapped[Decimal | None] = mapped_column(Numeric(12, 10), nullable=True)
    minimum_followable_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 10), nullable=True)
    effective_minimum_win_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 10), nullable=True)
    preview_id: Mapped[UUID | None] = mapped_column(ForeignKey("admin_threshold_previews.id"), nullable=True)
    is_removal: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(String(500))
    created_by_account_id: Mapped[UUID] = mapped_column(ForeignKey("app_accounts.id"))
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GlobalControl(Base):
    __tablename__ = "global_controls"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    reason: Mapped[str] = mapped_column(String(500))
    updated_by_account_id: Mapped[UUID] = mapped_column(ForeignKey("app_accounts.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_account_id: Mapped[UUID] = mapped_column(ForeignKey("app_accounts.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str] = mapped_column(String(80))
    old_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(String(500))
    request_id: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(UTC)
    )
```

`ThresholdPreview` is Plan 03's admin approval record, so its physical table is deliberately `admin_threshold_previews`. It is not a mapping of Plan 01's differently shaped `threshold_previews`; `ThresholdConfig.preview_id` must reference only `admin_threshold_previews.id`.

Create `models/device_tasks.py` with:

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, JSON, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from champion_follow_server.db.base import Base, UtcTimestampMixin, new_uuid


class TaskAction(StrEnum):
    BET = "BET"
    CANCEL = "CANCEL"


class DeviceTaskRevision(UtcTimestampMixin, Base):
    __tablename__ = "device_task_revisions"
    __table_args__ = (
        UniqueConstraint("device_id", "period_id", "revision", name="uq_task_revision"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    period_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[TaskAction] = mapped_column(Enum(TaskAction, native_enum=False))
    payload: Mapped[dict] = mapped_column(JSON)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    signing_key_version: Mapped[str] = mapped_column(String(32))
    signature: Mapped[bytes] = mapped_column(LargeBinary(64))
    canonical_sha256: Mapped[bytes] = mapped_column(LargeBinary(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeviceTaskHead(Base):
    __tablename__ = "device_task_heads"
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    period_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_task_revisions.id", ondelete="CASCADE"), unique=True
    )
```

`models/__init__.py` imports `auth`, `admin`, and `device_tasks` for their mapping side effects so Alembic sees exactly the Task 2 SQLAlchemy-owned tables. It must not import Plan 01 mappings as migration-owned metadata.

Add a partial unique migration index on `threshold_configs.scope_key WHERE is_active`, and keep history by setting the old row inactive and inserting a new version. An override-removal row has `is_removal=true`, null metric fields, and becomes the active device-scope record; effective-config lookup then falls back to the active global row.

```python
op.create_index(
    "uq_threshold_configs_active_scope",
    "threshold_configs",
    ["scope_key"],
    unique=True,
    postgresql_where=sa.text("is_active"),
)
```

- [ ] **Step 5: Generate and harden migration 0003**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  alembic revision --rev-id 0003_auth_admin_tasks -m "add auth admin and signed task state"
```

Do not use `--autogenerate`: Plan 01 owns existing tables outside this SQLAlchemy metadata, so an unfiltered diff could propose destructive drops. Keep `revision = "0003_auth_admin_tasks"` and `down_revision = None`, rename the generated file to `0003_auth_admin_tasks.py`, then handwrite the complete upgrade/downgrade and add these PostgreSQL invariants to `upgrade()`:

```python
op.create_check_constraint(
    "ck_admin_slot_matches_role",
    "app_accounts",
    "(role = 'ADMIN' AND admin_slot = 1) OR (role = 'USER' AND admin_slot IS NULL)",
)
op.create_check_constraint(
    "ck_authorization_code_target",
    "authorization_codes",
    "(purpose = 'REGISTER' AND target_account_id IS NULL) OR "
    "(purpose = 'REBIND' AND target_account_id IS NOT NULL)",
)
op.execute("CREATE SEQUENCE threshold_config_version_seq START WITH 1")
op.execute("""
CREATE FUNCTION reject_audit_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_events are append-only';
END;
$$ LANGUAGE plpgsql;
""")
op.execute("""
CREATE TRIGGER audit_events_no_update_delete
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
""")
```

`0003_auth_admin_tasks.py` is the only Alembic revision owned by this plan and must be complete before it is first applied. In the same `upgrade()`, create the assignment and client-ledger tables that Tasks 10 and 12 map and use; do **not** edit an already-applied `0003` later:

- `admin_threshold_previews`: Plan 03's admin/proposal binding rows exactly as mapped above; `threshold_configs.preview_id` references this table. Never create, alter, reference through a Plan 03 foreign key, or drop Plan 01's `threshold_previews` table;

- `assignment_rounds`: UUID primary key, unique `period_id`, allocation-seed version, 32-byte enabled-device/candidate-snapshot/manifest digests, and UTC creation time;
- `device_assignments`: UUID primary key, round/device IDs, a UUID `candidate_id` referencing Plan 01 `asof_candidates(id)`, candidate statistics version, period, device-specific followable rate, priority index, ball, direction, nullable task ID/revision, execution state and UTC timestamps. Enforce unique `(round_id, device_id)`, unique `(device_id, period_id, candidate_id)`, ball `1..5`, and state in `PLANNED/SUBMITTING/CONFIRMED/SKIPPED/CANCELLED`;
- `pair_sequence_counters`: canonical ordered device pair, nullable last ball/direction, count `0..3`, last period and optimistic-lock version; enforce one row per ordered pair and `device_a_id < device_b_id`;
- `device_event_cursors`: one row per device with binding epoch, non-negative acknowledged client sequence, last accepted 32-byte event digest and UTC update time;
- `device_events`: event UUID primary key plus device, binding epoch, positive client sequence, discriminator, observed/received UTC times, sanitized JSON payload, 32-byte canonical payload digest and DER ECDSA signature. Enforce unique `(device_id, client_seq)` and `(device_id, event_id)`;
- `orders`: UUID primary key plus device, exact task/revision, period, generation, local client-order ID, one-way platform-order reference, status in `CONFIRMED/REJECTED/UNKNOWN`, positive `stake_minor`, nullable confirmation event and UTC confirmation time. Enforce one `CONFIRMED` order per `(device_id, period_id)` with a partial unique index and uniqueness of each local order ID within a device;
- `settlements`: UUID primary key, unique order and event references, outcome in `WIN/LOSS/PUSH`, signed integer `net_pnl_minor`, and UTC settlement time;
- `balance_snapshots`: UUID primary key plus unique event, device, availability in `AVAILABLE/UNAVAILABLE`, nullable observed balance and UTC observed time. Enforce that balance is present exactly when availability is `AVAILABLE`;
- `bankroll_telemetry`: UUID primary key plus unique event, device, non-negative base/cap/unrecovered-loss/next-stake minor units, cycle UUID/version, frozen reason and UTC observed time;
- `latency_samples`: UUID primary key plus unique event, device, nullable task, bounded segment enum, non-negative milliseconds and UTC observed time.

All assignment/ledger foreign keys point to the auth/task tables in this revision or the existing Plan 01 `asof_candidates` table. The revision must neither create nor alter Plan 01 core tables. Tasks 10 and 12 add matching SQLAlchemy mappings and services only; their acceptance tests compare mapped columns and constraints to this migration so schema drift fails deterministically.

In `downgrade()`, drop the trigger before the function, drop `threshold_config_version_seq`, then drop only Plan 03-owned tables in reverse foreign-key order. It must never emit `drop_table`, `drop_column`, `drop_constraint` or `drop_index` for any `PLAN01_OWNED_TABLES` entry; in particular, `anonymous_actors`, `asof_candidates` and Plan 01 `threshold_previews` survive a full Plan 03 downgrade unchanged.

- [ ] **Step 6: Apply the migration and run model tests**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml up -d postgres
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server alembic upgrade head
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_auth_models.py -q
```

Expected: migration succeeds and `4 passed`; the table-manifest assertion proves the prefixed admin preview, assignment and ledger schema exists before later services are added.

- [ ] **Step 7: Commit schema state**

```bash
git add apps/champion_follow_platform/server/src/champion_follow_server/models \
  apps/champion_follow_platform/server/alembic/versions/0003_auth_admin_tasks.py \
  apps/champion_follow_platform/server/tests/test_auth_models.py
git commit -m "feat: add authorization, task, assignment and ledger schema"
```

---

### Task 3: Implement password, token, vault, device-key, and task-signing primitives

**Files:**
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/security/passwords.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/security/secrets.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/security/device_keys.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/security/task_signing.py`
- Test: `apps/champion_follow_platform/server/tests/test_passwords_and_secrets.py`
- Test: `apps/champion_follow_platform/server/tests/test_task_signing.py`

- [ ] **Step 1: Write primitive security tests using deterministic fake keys**

```python
# tests/test_passwords_and_secrets.py
from champion_follow_server.security.passwords import PasswordHasher
from champion_follow_server.security.secrets import SecretDigester, SecretVault


def test_password_hash_and_secret_digest_do_not_contain_plaintext() -> None:
    password = "test-password-not-used-by-any-account"
    password_hash = PasswordHasher().hash(password)
    digester = SecretDigester(b"test-only-pepper-with-more-than-32-bytes")
    digest = digester.digest("CF1-test-code-with-enough-entropy-123456")
    assert password not in password_hash
    assert PasswordHasher().verify(password_hash, password)
    assert digest != b"CF1-test-code-with-enough-entropy-123456"


def test_vault_round_trip_uses_random_nonce() -> None:
    vault = SecretVault(b"v" * 32)
    first = vault.encrypt(b"fake-totp-seed")
    second = vault.encrypt(b"fake-totp-seed")
    assert first != second
    assert vault.decrypt(first) == b"fake-totp-seed"
```

```python
# tests/test_task_signing.py
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from champion_follow_server.security.task_signing import TaskSigner, canonical_task_bytes


def test_signature_covers_revision_and_action() -> None:
    signer = TaskSigner(Ed25519PrivateKey.from_private_bytes(bytes(range(32))), "test-v1")
    envelope = {
        "device_id": "00000000-0000-0000-0000-000000000001",
        "period_id": "2607270001",
        "revision": 4,
        "action": "CANCEL",
        "expires_at": "2026-07-27T12:00:00Z",
        "payload": {"reason": "champion_withdrew"},
        "signing_key_version": "test-v1",
    }
    signature = signer.sign(envelope)
    signer.public_key.verify(signature, canonical_task_bytes(envelope))
    envelope["revision"] = 3
    try:
        signer.public_key.verify(signature, canonical_task_bytes(envelope))
    except Exception:
        pass
    else:
        raise AssertionError("mutated revision verified")
```

- [ ] **Step 2: Run tests and verify imports fail**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_passwords_and_secrets.py tests/test_task_signing.py -q
```

Expected: collection FAIL because the four security modules do not exist.

- [ ] **Step 3: Implement canonical task signing**

Use Argon2id defaults from `argon2.PasswordHasher`, `hmac.new(pepper, value, sha256)`, AES-GCM with a fresh 12-byte nonce, ECDSA P-256 SPKI DER for device keys, and Ed25519 only for the server task-signing key. Resolve `Settings.token_pepper.get_secret_value()` only at `SecretDigester` construction and never log or retain the plaintext setting elsewhere. Canonical task bytes are UTF-8 JSON with sorted keys, compact separators and `allow_nan=False`:

```python
# security/task_signing.py
import json
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical_task_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


class TaskSigner:
    def __init__(self, private_key: Ed25519PrivateKey, key_version: str) -> None:
        self._private_key = private_key
        self.public_key = private_key.public_key()
        self.key_version = key_version

    def sign(self, envelope: Mapping[str, Any]) -> bytes:
        if envelope.get("signing_key_version") != self.key_version:
            raise ValueError("signing key version mismatch")
        return self._private_key.sign(canonical_task_bytes(envelope))
```

- [ ] **Step 4: Implement Argon2 password hashing and AES-GCM secret storage**

Use these complete implementations:

```python
# security/passwords.py
from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class PasswordHasher:
    def __init__(self) -> None:
        self._hasher = Argon2Hasher(
            time_cost=3,
            memory_cost=65_536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
        )

    def hash(self, plaintext: str) -> str:
        return self._hasher.hash(plaintext)

    def verify(self, encoded: str, plaintext: str) -> bool:
        try:
            return self._hasher.verify(encoded, plaintext)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, encoded: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(encoded)
        except InvalidHashError:
            return True
```

```python
# security/secrets.py
import hashlib
import hmac
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretDigester:
    def __init__(self, pepper: bytes) -> None:
        if len(pepper) < 32:
            raise ValueError("pepper must contain at least 32 bytes")
        self._pepper = pepper

    def digest(self, plaintext: str) -> bytes:
        return hmac.new(self._pepper, plaintext.encode("utf-8"), hashlib.sha256).digest()

    def matches(self, stored: bytes, plaintext: str) -> bool:
        return hmac.compare_digest(stored, self.digest(plaintext))

    @staticmethod
    def generate_token() -> str:
        return secrets.token_urlsafe(32)


class SecretVault:
    VERSION = b"\x01"

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("vault key must contain exactly 32 bytes")
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext, self.VERSION)
        return self.VERSION + nonce + ciphertext

    def decrypt(self, packed: bytes) -> bytes:
        if len(packed) < 30 or packed[:1] != self.VERSION:
            raise ValueError("unsupported encrypted value")
        nonce = packed[1:13]
        return self._cipher.decrypt(nonce, packed[13:], self.VERSION)
```

- [ ] **Step 5: Implement bounded ECDSA P-256 device-proof verification**

```python
# security/device_keys.py
import base64
import hashlib
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


class InvalidDeviceProof(ValueError):
    pass


def _decode_bounded(value: str, minimum: int, maximum: int) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidDeviceProof("invalid device proof") from exc
    if not minimum <= len(decoded) <= maximum:
        raise InvalidDeviceProof("invalid device proof")
    return decoded


def enrollment_message(challenge_id: UUID, nonce: bytes) -> bytes:
    if len(nonce) != 32:
        raise InvalidDeviceProof("invalid device proof")
    return b"champion-follow-device-bind-v1\x00" + challenge_id.bytes + b"\x00" + nonce


def verify_device_proof(
    *, challenge_id: UUID, nonce: bytes, public_key_spki_der_b64: str, proof_der_b64: str
) -> tuple[bytes, bytes]:
    spki_der = _decode_bounded(public_key_spki_der_b64, 80, 256)
    signature = _decode_bounded(proof_der_b64, 64, 80)
    try:
        public_key = serialization.load_der_public_key(spki_der)
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise InvalidDeviceProof("invalid device proof")
        if not isinstance(public_key.curve, ec.SECP256R1):
            raise InvalidDeviceProof("invalid device proof")
        canonical = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if canonical != spki_der:
            raise InvalidDeviceProof("invalid device proof")
        public_key.verify(
            signature,
            enrollment_message(challenge_id, nonce),
            ec.ECDSA(hashes.SHA256()),
        )
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise InvalidDeviceProof("invalid device proof") from exc
    return spki_der, hashlib.sha256(spki_der).digest()
```

- [ ] **Step 6: Load only an Ed25519 task-signing private key**

Add `load_task_signer(path, key_version)` to `task_signing.py`: read PEM bytes, call `serialization.load_pem_private_key(pem_bytes, password=None)`, reject any key that is not `Ed25519PrivateKey`, and return `TaskSigner`. Never include PEM content or the underlying loader exception in logs.

`security/device_keys.py` accepts canonical SubjectPublicKeyInfo DER for exactly an ECDSA P-256 public key, rejects every other curve/algorithm, computes `sha256(spki_der).digest()`, and verifies an ASN.1 DER ECDSA-SHA256 signature over:

```python
def enrollment_message(challenge_id: UUID, nonce: bytes) -> bytes:
    return b"champion-follow-device-bind-v1\x00" + challenge_id.bytes + b"\x00" + nonce
```

Reject malformed Base64, non-canonical SPKI, non-P-256 keys, malformed DER signatures and invalid proofs with one generic `InvalidDeviceProof` exception. Never include supplied bytes in the exception message.

The Windows client must export the CNG key with `ECDsa.ExportSubjectPublicKeyInfo()` and Base64-encode those DER bytes; PEM, a CNG private-key blob, raw `x||y` coordinates, Ed25519 keys and IEEE-P1363 signatures are rejected. Both enrollment and client-event proofs use the RFC 3279 ASN.1 DER ECDSA-SHA256 signature format.

- [ ] **Step 7: Run the primitive tests**

Run the Step 2 command again.

Expected: `4 passed`.

- [ ] **Step 8: Commit security primitives**

```bash
git add apps/champion_follow_platform/server/src/champion_follow_server/security \
  apps/champion_follow_platform/server/tests/test_passwords_and_secrets.py \
  apps/champion_follow_platform/server/tests/test_task_signing.py
git commit -m "feat: add champion platform security primitives"
```

---

### Task 4: Add redacted immutable audit writes

**Files:**
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/services/audit.py`
- Test: `apps/champion_follow_platform/server/tests/test_audit.py`

- [ ] **Step 1: Write tests that reject secret-shaped audit fields and database mutation**

```python
# tests/test_audit.py
import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

from champion_follow_server.models.admin import AuditEvent
from champion_follow_server.models.auth import Account, AccountRole, AccountStatus
from champion_follow_server.services.audit import AuditWriter, UnsafeAuditPayload


@pytest.mark.asyncio
async def test_audit_rejects_secret_field_names(db_session) -> None:
    admin_account = Account(
        username_canonical="audit-admin-1",
        password_hash="test-hash",
        role=AccountRole.ADMIN,
        status=AccountStatus.ACTIVE,
        admin_slot=1,
    )
    db_session.add(admin_account)
    await db_session.flush()
    writer = AuditWriter()
    with pytest.raises(UnsafeAuditPayload):
        await writer.append(
            db_session,
            actor_account_id=admin_account.id,
            action="AUTH_CODE_CREATED",
            target_type="authorization_code",
            target_id="test-id",
            old_state=None,
            new_state={"authorization_code": "must-not-be-recorded"},
            reason="test",
            request_id="request-1",
        )


@pytest.mark.asyncio
async def test_committed_audit_row_cannot_be_updated_or_deleted(db_session) -> None:
    admin_account = Account(
        username_canonical="audit-admin-2",
        password_hash="test-hash",
        role=AccountRole.ADMIN,
        status=AccountStatus.ACTIVE,
        admin_slot=1,
    )
    db_session.add(admin_account)
    await db_session.flush()
    audit_event = await AuditWriter().append(
        db_session,
        actor_account_id=admin_account.id,
        action="GLOBAL_STOP_CHANGED",
        target_type="global_control",
        target_id="global-stop",
        old_state={"enabled": False},
        new_state={"enabled": True},
        reason="test safety stop",
        request_id="request-2",
    )
    await db_session.commit()
    with pytest.raises(DBAPIError):
        await db_session.execute(
            update(AuditEvent).where(AuditEvent.id == audit_event.id).values(reason="changed")
        )
        await db_session.commit()
```

- [ ] **Step 2: Run the audit tests and verify failure**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_audit.py -q
```

Expected: FAIL because `AuditWriter` does not exist.

- [ ] **Step 3: Implement recursive redaction refusal and append-only writing**

```python
# services/audit.py
from collections.abc import Mapping, Sequence
from uuid import UUID

from champion_follow_server.models.admin import AuditEvent

FORBIDDEN_PARTS = frozenset(
    {"password", "token", "secret", "cookie", "authorization_code", "private_key", "totp"}
)


class UnsafeAuditPayload(ValueError):
    pass


def assert_audit_safe(value: object, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in FORBIDDEN_PARTS):
                raise UnsafeAuditPayload(f"forbidden audit field at {path}")
            assert_audit_safe(child, f"{path}.{normalized}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            assert_audit_safe(child, path)


class AuditWriter:
    async def append(
        self,
        session,
        *,
        actor_account_id: UUID,
        action: str,
        target_type: str,
        target_id: str,
        old_state: dict | None,
        new_state: dict | None,
        reason: str,
        request_id: str,
    ) -> AuditEvent:
        assert_audit_safe(old_state)
        assert_audit_safe(new_state)
        row = AuditEvent(
            actor_account_id=actor_account_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            old_state=old_state,
            new_state=new_state,
            reason=reason.strip(),
            request_id=request_id,
        )
        session.add(row)
        await session.flush()
        return row
```

The service never commits independently; the audit row and the state change commit in the same caller transaction.

- [ ] **Step 4: Run audit tests**

Run the Step 2 command again.

Expected: `2 passed`.

- [ ] **Step 5: Commit the audit boundary**

```bash
git add apps/champion_follow_platform/server/src/champion_follow_server/services/audit.py \
  apps/champion_follow_platform/server/tests/test_audit.py
git commit -m "feat: add immutable redacted admin audit"
```

---

### Task 5: Bootstrap and authenticate the sole TOTP administrator

**Files:**
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/security/totp.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/services/admin_bootstrap.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/cli/admin.py`
- Create: `apps/champion_follow_platform/server/tests/test_admin_totp.py`

- [ ] **Step 1: Write failing sole-admin and bounded-TOTP tests**

```python
# tests/test_admin_totp.py
from datetime import UTC, datetime

from urllib.parse import parse_qs, urlparse

import pyotp
import pytest

from champion_follow_server.services.admin_bootstrap import AdminAlreadyExists, AdminBootstrapService


@pytest.mark.asyncio
async def test_bootstrap_creates_one_admin_and_requires_current_totp(
    db_session, password_hasher, secret_vault
) -> None:
    service = AdminBootstrapService(password_hasher, secret_vault)
    result = await service.create_pending_admin(
        db_session,
        username="owner",
        password="test-admin-password-with-16-chars",
        issuer="Champion Follow",
    )
    seed = parse_qs(urlparse(result.provisioning_uri).query)["secret"][0]
    otp = pyotp.TOTP(seed).at(1_785_136_800)
    await service.confirm_totp(db_session, result.account_id, otp, now=datetime.fromtimestamp(1_785_136_800, UTC))
    with pytest.raises(AdminAlreadyExists):
        await service.create_pending_admin(
            db_session,
            username="owner-2",
            password="another-test-admin-password",
            issuer="Champion Follow",
        )
```

`provisioning_uri` is marked `repr=False`, exists only in this local service/CLI bootstrap result, and is absent from every HTTP schema.

- [ ] **Step 2: Run the TOTP test and verify failure**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_admin_totp.py -q
```

Expected: collection FAIL because `AdminBootstrapService` is absent.

- [ ] **Step 3: Implement bounded TOTP verification**

Create `security/totp.py` with this implementation. It requires exactly six ASCII digits, decrypts the seed only for verification, bounds drift to one 30-second window, and mutates the account's bounded lock state without logging the supplied code or decrypted seed:

```python
from datetime import datetime, timedelta

import pyotp

from champion_follow_server.models.auth import Account, AdminTotp
from champion_follow_server.security.secrets import SecretVault


class TotpVerifier:
    def __init__(self, vault: SecretVault) -> None:
        self._vault = vault

    def verify(
        self,
        *,
        account: Account,
        totp: AdminTotp,
        code: str,
        now: datetime,
    ) -> bool:
        if account.locked_until is not None and account.locked_until > now:
            return False
        valid_shape = len(code) == 6 and code.isascii() and code.isdigit()
        secret = self._vault.decrypt(totp.secret_ciphertext).decode("ascii")
        valid = valid_shape and pyotp.TOTP(secret).verify(code, for_time=now, valid_window=1)
        if valid:
            account.failed_login_count = 0
            account.locked_until = None
            return True
        account.failed_login_count += 1
        if account.failed_login_count >= 5:
            account.locked_until = now + timedelta(minutes=15)
            account.failed_login_count = 0
        return False
```

- [ ] **Step 4: Implement the sole-admin bootstrap service**

Create `services/admin_bootstrap.py` and `cli/admin.py`. The service:

1. obtains a PostgreSQL advisory transaction lock named `champion-follow-sole-admin`;
2. refuses creation if any `admin_slot=1` row exists;
3. hashes the password, encrypts the generated TOTP seed, and inserts the pending admin;
4. returns the provisioning URI only to the local CLI result;
5. confirms one valid OTP before setting `AdminTotp.confirmed_at` and `Account.status=ACTIVE`.

The service core is:

```python
# services/admin_bootstrap.py
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import pyotp
from sqlalchemy import select, text

from champion_follow_server.models.auth import Account, AccountRole, AccountStatus, AdminTotp
from champion_follow_server.security.passwords import PasswordHasher
from champion_follow_server.security.secrets import SecretVault
from champion_follow_server.security.totp import TotpVerifier


class AdminAlreadyExists(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PendingAdmin:
    account_id: UUID
    provisioning_uri: str = field(repr=False)


class AdminBootstrapService:
    def __init__(self, password_hasher: PasswordHasher, vault: SecretVault) -> None:
        self._password_hasher = password_hasher
        self._vault = vault

    async def create_pending_admin(
        self, session, *, username: str, password: str, issuer: str
    ) -> PendingAdmin:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('champion-follow-sole-admin', 0))")
        )
        existing = await session.scalar(select(Account.id).where(Account.admin_slot == 1))
        if existing is not None:
            raise AdminAlreadyExists("administrator already initialized")
        canonical = username.strip().casefold()
        secret = pyotp.random_base32(length=32)
        account = Account(
            username_canonical=canonical,
            password_hash=self._password_hasher.hash(password),
            role=AccountRole.ADMIN,
            status=AccountStatus.PENDING,
            admin_slot=1,
        )
        session.add(account)
        await session.flush()
        session.add(
            AdminTotp(
                account_id=account.id,
                secret_ciphertext=self._vault.encrypt(secret.encode("ascii")),
            )
        )
        await session.flush()
        uri = pyotp.TOTP(secret).provisioning_uri(name=canonical, issuer_name=issuer)
        return PendingAdmin(account.id, uri)

    async def confirm_totp(
        self, session, account_id: UUID, code: str, *, now: datetime | None = None
    ) -> None:
        current = now or datetime.now(UTC)
        account = await session.scalar(select(Account).where(Account.id == account_id).with_for_update())
        totp = await session.get(AdminTotp, account_id)
        if account is None or totp is None or not TotpVerifier(self._vault).verify(
            account=account, totp=totp, code=code, now=current
        ):
            raise ValueError("administrator confirmation failed")
        account.status = AccountStatus.ACTIVE
        totp.confirmed_at = current
        await session.flush()
```

- [ ] **Step 5: Implement the secure local QR CLI**

The CLI command is:

```bash
champion-admin bootstrap --username owner --qr-output /var/lib/champion-follow/admin-totp.png
```

It prompts twice with `getpass`, calls `create_pending_admin`, passes the returned URI directly to `qrcode.make`, writes the QR PNG with mode `0600`, prints only the output path, prompts for the six-digit OTP, calls `confirm_totp`, commits, deletes the QR on success, and exits non-zero if confirmation fails. It never prints the provisioning URI or seed, and deletes the local URI variable immediately after QR construction.

After successful confirmation, append `ADMIN_BOOTSTRAPPED` with the new admin account as actor/target, public username only, reason `initial sole administrator bootstrap`, and no TOTP material. Commit the account, TOTP confirmation and audit event together.

- [ ] **Step 6: Run the TOTP test and CLI help smoke test**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_admin_totp.py -q
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  champion-admin --help
```

Expected: `1 passed`; CLI exits 0 and lists only the `bootstrap` command.

- [ ] **Step 7: Commit sole-admin TOTP**

```bash
git add apps/champion_follow_platform/server/src/champion_follow_server/security/totp.py \
  apps/champion_follow_platform/server/src/champion_follow_server/services/admin_bootstrap.py \
  apps/champion_follow_platform/server/src/champion_follow_server/cli/admin.py \
  apps/champion_follow_platform/server/tests/test_admin_totp.py
git commit -m "feat: bootstrap sole totp administrator"
```

---

### Task 6: Implement one-time authorization codes and device binding

**Files:**
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/services/authorization_codes.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/services/device_binding.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/schemas/auth.py`
- Create: `apps/champion_follow_platform/server/tests/factories/auth.py`
- Create: `apps/champion_follow_platform/server/tests/test_authorization_codes.py`
- Create: `apps/champion_follow_platform/server/tests/test_device_binding.py`

- [ ] **Step 1: Write tests for one-time consumption and proof-of-possession**

```python
# tests/test_authorization_codes.py
import pytest

from champion_follow_server.models.auth import CodePurpose
from champion_follow_server.services.authorization_codes import AuthorizationCodeService, CodeUnavailable


@pytest.mark.asyncio
async def test_code_is_returned_once_and_consumed_once(db_session, admin_account, digester, audit_writer, clock) -> None:
    service = AuthorizationCodeService(digester, audit_writer, clock)
    issued = await service.issue(
        db_session,
        actor=admin_account,
        purpose=CodePurpose.REGISTER,
        target_account_id=None,
        reason="new licensed user",
        request_id="request-1",
    )
    assert issued.plaintext.startswith("CF1-")
    assert issued.plaintext.encode() not in repr(issued.row).encode()
    await service.consume(db_session, plaintext=issued.plaintext, expected_purpose=CodePurpose.REGISTER)
    with pytest.raises(CodeUnavailable):
        await service.consume(db_session, plaintext=issued.plaintext, expected_purpose=CodePurpose.REGISTER)
```

```python
# tests/test_device_binding.py
import base64

import pytest

from champion_follow_server.security.device_keys import enrollment_message
from champion_follow_server.services.device_binding import DeviceBindingService, InvalidEnrollment


@pytest.mark.asyncio
async def test_registration_consumes_code_and_binds_verified_key(
    db_session, registration_code, fake_device_keypair, binding_service, clock
) -> None:
    challenge = await binding_service.create_challenge(db_session, registration_code.plaintext)
    signature = base64.b64encode(
        fake_device_keypair.private_key.sign(enrollment_message(challenge.id, challenge.nonce))
    ).decode("ascii")
    result = await binding_service.register(
        db_session,
        code_plaintext=registration_code.plaintext,
        challenge_id=challenge.id,
        username="licensed-user",
        password="test-user-password-with-16-chars",
        public_key_spki_der_b64=fake_device_keypair.public_key_spki_der_b64,
        proof_der_b64=signature,
    )
    assert result.device.account_id == result.account.id
    assert result.device.public_key_spki_der == fake_device_keypair.public_key_spki_der


@pytest.mark.asyncio
async def test_registration_rejects_signature_from_other_key(
    db_session, registration_code, fake_device_keypair, another_device_keypair, binding_service
) -> None:
    challenge = await binding_service.create_challenge(db_session, registration_code.plaintext)
    wrong = base64.b64encode(
        another_device_keypair.private_key.sign(enrollment_message(challenge.id, challenge.nonce))
    ).decode("ascii")
    with pytest.raises(InvalidEnrollment):
        await binding_service.register(
            db_session,
            code_plaintext=registration_code.plaintext,
            challenge_id=challenge.id,
            username="licensed-user",
            password="test-user-password-with-16-chars",
            public_key_spki_der_b64=fake_device_keypair.public_key_spki_der_b64,
            proof_der_b64=wrong,
        )
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_authorization_codes.py tests/test_device_binding.py -q
```

Expected: collection FAIL because the services do not exist.

- [ ] **Step 3: Implement authorization-code issue**

Generate plaintext with `"CF1-" + secrets.token_urlsafe(32)`, persist `digester.digest(plaintext)`, purpose, target and expiry, and return plaintext only in an `IssuedCode` value whose plaintext field has `repr=False`. Append `AUTH_CODE_CREATED` with row ID, purpose, target and expiry; do not include plaintext or digest.

- [ ] **Step 4: Implement row-locked authorization-code lookup**

Select `AuthorizationCode` by digest using `.with_for_update()`. Reject wrong purpose/target, expiry and prior consumption with the same `CodeUnavailable("authorization code unavailable")`. Keep `validate()` non-consuming for challenge creation and `consume()` caller-transactional.

- [ ] **Step 5: Run authorization-code tests**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_authorization_codes.py -q
```

Expected: authorization-code tests PASS.

- [ ] **Step 6: Implement five-minute enrollment challenges**

`DeviceBindingService.create_challenge()` validates without consuming the code, marks earlier live challenges for that code consumed, writes a fresh 32-byte nonce and five-minute expiry, and returns only challenge ID plus Base64 nonce.

- [ ] **Step 7: Implement REGISTER binding**

In one transaction, canonicalize username with Unicode NFKC plus `casefold()`, row-lock and revalidate code/challenge, verify ECDSA P-256 proof, create user plus active device, consume code/challenge, and append `ACCOUNT_REGISTERED` plus `DEVICE_BOUND` audit rows containing IDs and public-key fingerprint only.

- [ ] **Step 8: Implement REBIND binding**

Require a code tied to the existing account plus the correct account password. Row-lock the old device, mark it `UNBOUND`, flush that update before inserting the new active device, set the new binding epoch to old epoch plus one, revoke old device sessions, and consume the code/challenge in the same transaction. Map all uniqueness/proof/auth failures to generic `InvalidEnrollment`.

- [ ] **Step 9: Add strict request/response schemas**

Define Pydantic models with `extra="forbid"`, username length 3–80, password length 12–128, Base64 length bounds, and no model `repr` containing password/code/proof fields. Responses contain challenge ID/nonce, account ID, device ID and fingerprint; they never echo authorization code, password or signature.

- [ ] **Step 10: Add the concurrent-reuse regression test**

Use two independent sessions to consume the same code concurrently and assert one success plus one `CodeUnavailable`.

- [ ] **Step 11: Run all enrollment tests**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_authorization_codes.py tests/test_device_binding.py -q
```

Expected: all tests PASS, including the concurrent-consumption test.

- [ ] **Step 12: Commit code enrollment and binding**

```bash
git add apps/champion_follow_platform/server/src/champion_follow_server/services/authorization_codes.py \
  apps/champion_follow_platform/server/src/champion_follow_server/services/device_binding.py \
  apps/champion_follow_platform/server/src/champion_follow_server/schemas/auth.py \
  apps/champion_follow_platform/server/tests/factories/auth.py \
  apps/champion_follow_platform/server/tests/test_authorization_codes.py \
  apps/champion_follow_platform/server/tests/test_device_binding.py
git commit -m "feat: bind licensed accounts to verified devices"
```

---

### Task 7: Add independently revocable user and admin sessions

**Files:**
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/services/sessions.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/api/dependencies.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/api/auth.py`
- Modify: `apps/champion_follow_platform/server/src/champion_follow_server/app.py`
- Test: `apps/champion_follow_platform/server/tests/test_sessions_api.py`

- [ ] **Step 1: Write failing user/admin authentication API tests**

```python
# tests/test_sessions_api.py
def test_user_login_requires_password_and_bound_device_proof(client, user_account, active_device, device_login_proof) -> None:
    response = client.post("/api/v1/auth/device/login", json=device_login_proof)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "refresh_token", "access_expires_at", "device_id"}
    assert body["device_id"] == str(active_device.id)


def test_admin_login_requires_totp_and_uses_httponly_refresh_cookie(client, confirmed_admin, current_admin_otp) -> None:
    response = client.post(
        "/api/v1/admin/session",
        json={"username": "owner", "password": "test-admin-password-with-16-chars", "totp": current_admin_otp},
        headers={"Origin": "https://console.example.test"},
    )
    assert response.status_code == 200
    assert response.json().keys() == {"access_token", "access_expires_at", "csrf_token"}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie


def test_disabled_account_session_is_rejected(client, disabled_user_access_token) -> None:
    response = client.get(
        "/api/v1/me/report",
        headers={"Authorization": f"Bearer {disabled_user_access_token}"},
    )
    assert response.status_code == 401
```

- [ ] **Step 2: Run the session tests and verify 404/import failures**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_sessions_api.py -q
```

Expected: FAIL because auth routes are not registered.

- [ ] **Step 3: Implement opaque session issue**

`SessionService.issue()` generates independent 32-byte URL-safe access, refresh and CSRF values and stores only digests. User rows contain device ID/current binding epoch; admin rows have no device and `kind=ADMIN`. Return plaintext in a `SessionTokenPair` with `repr=False` fields.

- [ ] **Step 4: Implement access lookup and immediate revocation checks**

`authenticate_access()` selects by digest and rejects expired/revoked sessions, disabled or pending accounts, unbound devices and binding-epoch mismatch. `revoke_account`, `revoke_device`, and `revoke_session` update matching live rows in the caller transaction.

- [ ] **Step 5: Implement one-time refresh rotation**

`rotate_refresh()` selects the digest with `FOR UPDATE`, revokes it and creates a new row sharing `family_id` plus `rotated_from_id`. Reusing a revoked refresh digest revokes every live session in that family before returning generic authentication failure.

- [ ] **Step 6: Implement bound-device login challenge**

Device login is a separate proof from enrollment: `/auth/device/challenge` creates `DeviceLoginChallenge`, and the bound key signs `b"champion-follow-device-login-v1\x00" + challenge_id.bytes + b"\x00" + nonce`. `/auth/device/login` row-locks and consumes that challenge, verifies the account password and signature, then issues a session bound to the current device ID and binding epoch. A challenge is single-use and expires after five minutes.

- [ ] **Step 7: Add authentication dependencies**

Create dependencies:

```python
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from champion_follow_server.db.session import get_session
from champion_follow_server.models.auth import Account, AccountRole, AuthSession, Device

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class UserContext:
    account: Account
    auth_session: AuthSession


@dataclass(frozen=True, slots=True)
class DeviceContext(UserContext):
    device: Device


@dataclass(frozen=True, slots=True)
class AdminContext(UserContext):
    pass


async def _context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    db_session,
) -> UserContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="authentication required")
    result = await request.app.state.session_service.authenticate_access(
        db_session, credentials.credentials
    )
    if result is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return UserContext(result.account, result.auth_session)


async def require_user_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db_session=Depends(get_session),
) -> UserContext:
    return await _context(request, credentials, db_session)


async def require_active_device_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db_session=Depends(get_session),
) -> DeviceContext:
    context = await _context(request, credentials, db_session)
    device = await request.app.state.session_service.active_device_for(
        db_session, context.auth_session
    )
    if device is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return DeviceContext(context.account, context.auth_session, device)


async def require_admin_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db_session=Depends(get_session),
) -> AdminContext:
    context = await _context(request, credentials, db_session)
    if context.account.role is not AccountRole.ADMIN:
        raise HTTPException(status_code=403, detail="administrator required")
    return AdminContext(context.account, context.auth_session)


async def require_admin_csrf(
    request: Request,
    context: AdminContext = Depends(require_admin_context),
) -> AdminContext:
    expected_origin = str(request.app.state.settings.trusted_admin_origin).rstrip("/")
    if request.headers.get("origin", "").rstrip("/") != expected_origin:
        raise HTTPException(status_code=403, detail="request rejected")
    supplied = request.headers.get("x-csrf-token", "")
    if not request.app.state.session_service.verify_csrf(context.auth_session, supplied):
        raise HTTPException(status_code=403, detail="request rejected")
    return context
```

`require_admin_csrf` compares the request `Origin` to `settings.trusted_admin_origin` and the `X-CSRF-Token` digest to the authenticated session using constant-time comparison. All failures return the same 401/403 shape without account, device or token details.

- [ ] **Step 8: Add enrollment and device-session routes**

Implement these routes with `extra="forbid"` schemas, `Cache-Control: no-store`, generic authentication errors, and transaction rollback on any proof/code/session error:

```text
POST /api/v1/enrollment/challenge
POST /api/v1/enrollment/register
POST /api/v1/enrollment/rebind
POST /api/v1/auth/device/challenge
POST /api/v1/auth/device/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/auth/task-signing-keys
```

The last route requires an active bound-device session and returns only `{"keys":[{"version":"task-v1","public_key_spki_der_b64":"...","sha256":"..."}]}` for the current Ed25519 signer. It derives SPKI DER public bytes from the loaded signer, uses padded standard Base64, and never reads or returns private-key bytes. Plan 04 must refresh this in-memory verification set after login/reconnect and fail closed on an unknown version; standard authenticated TLS is the bootstrap trust boundary for v1. Key rotation is a later versioned migration, not an implicit second key in this release.

- [ ] **Step 9: Add administrator-session routes and cookies**

Implement:

```text
POST /api/v1/admin/session
POST /api/v1/admin/session/refresh
DELETE /api/v1/admin/session
```

Admin refresh tokens are set only as `__Host-champion_admin_refresh` with `Secure; HttpOnly; Path=/; SameSite=Strict`; never return them in JSON. User refresh tokens are returned once for the Electron client to put in Windows Credential Manager in plan 04.

- [ ] **Step 10: Add lockout tests**

Test five bad login attempts cause a 15-minute lock and a valid admin TOTP after the lock expires resets the failure counter.

- [ ] **Step 11: Add rotation and revocation tests**

Test refresh rotation invalidates the prior token, reuse revokes the family, device unbind invalidates access immediately, and captured logs contain none of the submitted credentials.

- [ ] **Step 12: Run session tests**

Run the Step 2 command again.

Expected: all session API tests PASS.

- [ ] **Step 13: Commit central authentication**

```bash
git add apps/champion_follow_platform/server/src/champion_follow_server/services/sessions.py \
  apps/champion_follow_platform/server/src/champion_follow_server/api/dependencies.py \
  apps/champion_follow_platform/server/src/champion_follow_server/api/auth.py \
  apps/champion_follow_platform/server/src/champion_follow_server/app.py \
  apps/champion_follow_platform/server/tests/test_sessions_api.py
git commit -m "feat: add revocable user and admin sessions"
```

---

### Task 8: Make threshold frequency preview mandatory before activation

**Files:**
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/services/thresholds.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/schemas/admin.py`
- Create: `apps/champion_follow_platform/server/tests/test_threshold_admin.py`

- [ ] **Step 1: Write failing exact-rate and preview-binding tests**

```python
# tests/test_threshold_admin.py
from decimal import Decimal

import pytest

from champion_follow_server.services.thresholds import PreviewMismatch, ThresholdProposal, effective_min_win_rate


def test_effective_minimum_uses_stricter_equivalent_condition() -> None:
    assert effective_min_win_rate(Decimal("0.5200000000"), Decimal("0.0500000000")) == Decimal("0.5357142858")


@pytest.mark.asyncio
async def test_activation_requires_unexpired_matching_preview(
    db_session, admin_account, threshold_service, frozen_preview_source, clock
) -> None:
    proposal = ThresholdProposal(
        minimum_level="FORMAL",
        minimum_conservative_win_rate=Decimal("0.5200000000"),
        minimum_conservative_roi=Decimal("0.0192000000"),
        minimum_followable_rate=Decimal("0.8000000000"),
    )
    preview = await threshold_service.preview(
        db_session, actor=admin_account, proposal=proposal, device_id=None, now=clock.now()
    )
    changed = proposal.model_copy(update={"minimum_followable_rate": Decimal("0.7000000000")})
    with pytest.raises(PreviewMismatch):
        await threshold_service.activate(
            db_session,
            actor=admin_account,
            proposal=changed,
            device_id=None,
            preview_id=preview.id,
            reason="activate global threshold",
            request_id="request-1",
            now=clock.now(),
        )
```

- [ ] **Step 2: Run the threshold tests and verify failure**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_threshold_admin.py -q
```

Expected: collection FAIL because `services.thresholds` is absent.

- [ ] **Step 3: Implement exact proposal canonicalization**

`ThresholdProposal` uses `Decimal`; win/follow rates must be in `[0, 1]`, ROI must be in `[-1, 0.96]`, and the minimum user level is one of `CANDIDATE/FORMAL/CORE` because observers are never executable. Serialize a canonical payload containing all four values as fixed ten-decimal strings. Compute:

```python
RATE_QUANTUM = Decimal("0.0000000001")


def effective_min_win_rate(min_win_rate: Decimal, min_roi: Decimal) -> Decimal:
    roi_as_win_rate = (min_roi + Decimal(1)) / Decimal("1.96")
    return max(min_win_rate, roi_as_win_rate).quantize(RATE_QUANTUM, rounding=ROUND_CEILING)
```

Hash the canonical proposal plus scope/device ID with SHA-256. The same proposal must yield the same digest on every platform.

- [ ] **Step 4: Persist an as-of preview**

`ThresholdService.preview()` calls Plan 01's `ThresholdPreviewService` through the prerequisite boundary shown at the top of this plan. The adapter maps `CANDIDATE/FORMAL/CORE` exactly to Plan 01 `candidate/formal/core`, maps `minimum_conservative_roi` to Plan 01 `minimum_conservative_unit_return`, and passes the two rate fields unchanged as `Decimal`; unknown values fail rather than falling through. It saves the 7-day and 30-day results, exact proposal digest, snapshot watermark and expiry, and returns a non-secret preview ID. It must not call Plan 02 or duplicate Plan 01's preview SQL.

- [ ] **Step 5: Activate one matching global config version**

`activate()` row-locks the preview and rejects expired, different-admin, different-device or different-proposal previews. Newer snapshots arriving after preview do not invalidate it; retain the exact preview watermark. Acquire a scope advisory transaction lock, deactivate the current global scope, allocate `config_version` with `nextval('threshold_config_version_seq')`, insert the new active row, and append old/new threshold audit state in the same transaction.

- [ ] **Step 6: Activate and remove device overrides**

Use the same transaction for a full device override. Removing it inserts an active `is_removal=true` device record with a new config version and audit event instead of deleting history.

- [ ] **Step 7: Implement effective-config lookup**

There is no implicit default. `get_effective(device_id)` returns `None` until a global config is activated; afterward it returns the latest device override if present, otherwise the latest global config.

- [ ] **Step 8: Test no-default and device override behavior**

Add tests asserting:

- no active global threshold means no executable task configuration;
- a full device override affects only that device;
- deleting the override activates the global value by inserting an audited override-removal record;

- [ ] **Step 9: Test frozen-preview and history behavior**

Add tests asserting:

- preview inputs come only from Plan 01 frozen `asof_candidates` rows and never today's profile state;
- threshold activation does not mutate prior task rows.

- [ ] **Step 10: Run threshold tests**

Run the Step 2 command again.

Expected: all threshold tests PASS.

- [ ] **Step 11: Commit preview-backed thresholds**

```bash
git add apps/champion_follow_platform/server/src/champion_follow_server/services/thresholds.py \
  apps/champion_follow_platform/server/src/champion_follow_server/schemas/admin.py \
  apps/champion_follow_platform/server/tests/test_threshold_admin.py
git commit -m "feat: require walk-forward preview for thresholds"
```

---

### Task 9: Persist and sign monotonic BET/CANCEL task revisions

**Files:**
- Create: `apps/champion_follow_platform/contracts/device-task-v1.schema.json`
- Create: `apps/champion_follow_platform/contracts/fixtures/device-task-bet-v1.json`
- Create: `apps/champion_follow_platform/contracts/fixtures/device-task-cancel-v1.json`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/schemas/device_tasks.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/services/device_task_revisions.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/services/task_hub.py`
- Test: `apps/champion_follow_platform/server/tests/test_device_task_revisions.py`

- [ ] **Step 1: Write failing revision and tombstone tests**

```python
# tests/test_device_task_revisions.py
import asyncio

import pytest

from champion_follow_server.schemas.device_tasks import BetPayload


@pytest.mark.asyncio
async def test_cancel_tombstone_remains_head_after_late_old_bet(
    db_session, active_device, revision_service, future_expiry
) -> None:
    bet = await revision_service.publish_bet(
        db_session,
        device_id=active_device.id,
        period_id="2607270001",
        payload=BetPayload(
            signal_id="00000000-0000-0000-0000-000000000010",
            signal_version=3,
            actor_ref="A000007",
            ball=2,
            direction="ODD",
            threshold_version=8,
            odds_micros=1_960_000,
            user_level="CORE",
            sample_count=618,
            conservative_win_rate="0.5431000000",
            conservative_unit_return="0.0645000000",
            followable_rate="0.8120000000",
        ),
        expires_at=future_expiry,
    )
    cancel = await revision_service.publish_cancel(
        db_session,
        device_id=active_device.id,
        period_id="2607270001",
        reason="champion_withdrew",
        expires_at=future_expiry,
    )
    assert (bet.revision, cancel.revision, cancel.action.value) == (1, 2, "CANCEL")
    head = await revision_service.current_head(db_session, active_device.id, "2607270001")
    assert head.revision == 2


@pytest.mark.asyncio
async def test_two_publishers_receive_distinct_monotonic_revisions(
    independent_sessions, active_device_id, revision_service_factory, future_expiry
) -> None:
    first_session, second_session = independent_sessions

    async def publish(session, reason):
        service = revision_service_factory()
        row = await service.publish_cancel(
            session,
            device_id=active_device_id,
            period_id="2607270002",
            reason=reason,
            expires_at=future_expiry,
        )
        await session.commit()
        return row

    results = await asyncio.gather(
        publish(first_session, "data_gap"),
        publish(second_session, "global_stop"),
    )
    assert sorted(result.revision for result in results) == [1, 2]
```

- [ ] **Step 2: Run revision tests and verify failure**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_device_task_revisions.py -q
```

Expected: collection FAIL because task schemas/service do not exist.

- [ ] **Step 3: Define discriminated BET/CANCEL envelopes**

`BetPayload` allows only one ball `1..5`, one of `BIG/SMALL/ODD/EVEN/PRIME/COMPOSITE`, fixed `odds_micros=1_960_000`, immutable signal/threshold versions, a short opaque `actor_ref`, and the frozen raw metrics needed by the client UI. Stake is deliberately absent because Plan 04 computes each device's amount from its own confirmed-settlement bankroll chain. `CancelPayload` contains only a reason enum. `SignedTaskEnvelope` includes `task_id`, `device_id`, `period_id`, positive `revision`, action, issued/expires UTC timestamps, signing-key version, action payload and URL-safe Base64 signature. Pydantic models use `extra="forbid"`.

Encode the 64-byte Ed25519 signature with `base64.urlsafe_b64encode(signature).decode("ascii")`, retaining its `==` padding so Python and Electron verify identical wire bytes. Canonical signing excludes only the `signature` property; every other property in the envelope is covered.

Normalize `issued_at` and `expires_at` before signing to UTC RFC 3339 with exactly six fractional digits and a terminal `Z` (for example `2026-07-27T04:00:00.000000Z`). Persist `issued_at`, `expires_at`, the payload, and `sha256(canonical_unsigned_bytes)` in `DeviceTaskRevision`. Every database reload reconstructs the same unsigned envelope with that exact formatter and must match `canonical_sha256` before sending; a mismatch fails closed instead of emitting an unverifiable task.

Save the cross-platform wire contract in `contracts/device-task-v1.schema.json` with this top-level structure; list all allowed cancel reasons from the invalidation tests in the `reason` enum:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://champion-follow.invalid/contracts/device-task-v1.schema.json",
  "title": "Champion Follow Signed Device Task v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "task_id", "device_id", "period_id", "revision", "action",
    "issued_at", "expires_at", "signing_key_version", "payload", "signature"
  ],
  "properties": {
    "task_id": {"type": "string", "format": "uuid"},
    "device_id": {"type": "string", "format": "uuid"},
    "period_id": {"type": "string", "minLength": 1, "maxLength": 64},
    "revision": {"type": "integer", "minimum": 1},
    "action": {"enum": ["BET", "CANCEL"]},
    "issued_at": {"type": "string", "format": "date-time"},
    "expires_at": {"type": "string", "format": "date-time"},
    "signing_key_version": {"type": "string", "pattern": "^[a-z0-9-]{1,32}$"},
    "signature": {"type": "string", "pattern": "^[A-Za-z0-9_-]{86}==$"},
    "payload": {"oneOf": [{"$ref": "#/$defs/bet"}, {"$ref": "#/$defs/cancel"}]}
  },
  "allOf": [
    {
      "if": {"properties": {"action": {"const": "BET"}}},
      "then": {"properties": {"payload": {"$ref": "#/$defs/bet"}}}
    },
    {
      "if": {"properties": {"action": {"const": "CANCEL"}}},
      "then": {"properties": {"payload": {"$ref": "#/$defs/cancel"}}}
    }
  ],
  "$defs": {
    "bet": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "signal_id", "signal_version", "actor_ref", "ball", "direction",
        "threshold_version", "odds_micros", "user_level", "sample_count",
        "conservative_win_rate", "conservative_unit_return", "followable_rate"
      ],
      "properties": {
        "signal_id": {"type": "string", "format": "uuid"},
        "signal_version": {"type": "integer", "minimum": 1},
        "actor_ref": {"type": "string", "pattern": "^A[0-9]{6,12}$"},
        "ball": {"type": "integer", "minimum": 1, "maximum": 5},
        "direction": {"enum": ["BIG", "SMALL", "ODD", "EVEN", "PRIME", "COMPOSITE"]},
        "threshold_version": {"type": "integer", "minimum": 1},
        "odds_micros": {"const": 1960000},
        "user_level": {"enum": ["CANDIDATE", "FORMAL", "CORE"]},
        "sample_count": {"type": "integer", "minimum": 0},
        "conservative_win_rate": {"type": "string", "pattern": "^(0|1)\\.[0-9]{10}$"},
        "conservative_unit_return": {"type": "string", "pattern": "^-?[0-9]+\\.[0-9]{10}$"},
        "followable_rate": {"type": "string", "pattern": "^(0|1)\\.[0-9]{10}$"}
      }
    },
    "cancel": {
      "type": "object",
      "additionalProperties": false,
      "required": ["reason"],
      "properties": {
        "reason": {
          "enum": [
            "champion_withdrew", "profile_downgraded", "threshold_changed",
            "collector_stale", "data_gap", "device_reassigned", "account_disabled",
            "device_unbound", "global_stop"
          ]
        }
      }
    }
  }
}
```

Write those two sanitized, fully signed test envelopes to the listed shared fixture files using only the dedicated test key. Add a test that loads the JSON object as `contract`, validates both fixture files with `Draft202012Validator(contract, format_checker=FormatChecker())`, removes the signature before canonical signing verification, and rejects an action/payload mismatch. The fixture private key stays only in test code and is excluded from production packages.

- [ ] **Step 4: Implement concurrency-safe revision allocation**

Within one transaction, first acquire `pg_advisory_xact_lock(hashtextextended('task:' || :device_id || ':' || :period_id, 0))`, then lock an existing `DeviceTaskHead` with `SELECT FOR UPDATE` and choose revision `1` or `head.revision + 1`. The advisory lock also serializes the first revision when no head row exists; `SELECT FOR UPDATE` alone cannot lock an absent row. Callers never supply revision.

- [ ] **Step 5: Implement signed BET append**

Within the same transaction, `publish_bet`:

1. validates active device, active effective threshold and disabled global stop;
2. canonicalizes the unsigned envelope and signs it;
3. inserts the immutable revision and updates the head;
4. flushes but does not broadcast or commit.

- [ ] **Step 6: Implement signed CANCEL tombstones**

`publish_cancel()` operates while global stop is active. If the current head is already a `CANCEL` with the same reason, return it without creating noise; otherwise sign, append and promote a higher revision.

- [ ] **Step 7: Implement post-commit notification hub**

`TaskHub` keeps a bounded `asyncio.Queue(maxsize=1)` per connected device. Publishing replaces an older queued notification with the newest task ID. The API/service orchestration must call `await session.commit()` before `hub.publish(device_id, task_id)`. If a process dies between commit and publish, reconnect synchronization reads `DeviceTaskHead`, so PostgreSQL remains authoritative.

- [ ] **Step 8: Add invalidation coverage**

Test that champion withdrawal, a threshold replacement that makes the current signal ineligible, account disable, device unbind, collector heartbeat expiry, data-gap marking, reassignment and global stop each call `publish_cancel()` for every affected live `BET` head. Assert each resulting task has a higher revision and a verifiable signature. A threshold replacement under which the current signal still qualifies must not cancel or rewrite its historical task.

- [ ] **Step 9: Run revision tests**

Run the Step 2 command again.

Expected: all revision and invalidation tests PASS.

- [ ] **Step 10: Commit signed task revisions**

```bash
git add apps/champion_follow_platform/server/src/champion_follow_server/schemas/device_tasks.py \
  apps/champion_follow_platform/server/src/champion_follow_server/services/device_task_revisions.py \
  apps/champion_follow_platform/server/src/champion_follow_server/services/task_hub.py \
  apps/champion_follow_platform/contracts/device-task-v1.schema.json \
  apps/champion_follow_platform/contracts/fixtures/device-task-bet-v1.json \
  apps/champion_follow_platform/contracts/fixtures/device-task-cancel-v1.json \
  apps/champion_follow_platform/server/tests/test_device_task_revisions.py
git commit -m "feat: sign monotonic bet and cancel revisions"
```

---

### Task 10: Deterministically allocate champion signals across devices

**Files:**
- Modify: `apps/champion_follow_platform/server/src/champion_follow_server/models/__init__.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/models/signals.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/models/assignments.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/services/device_allocator.py`
- Create: `apps/champion_follow_platform/server/tests/test_device_allocator.py`

- [ ] **Step 1: Write allocation-cap, rotation, and replay RED tests**

```python
@pytest.mark.asyncio
async def test_three_devices_rotate_first_priority_and_replay_identically(
    db_session, allocator, three_enabled_devices, three_candidates
) -> None:
    first_counts = {device.id: 0 for device in three_enabled_devices}
    digests = []
    for index in range(300):
        issue = f"260727{index:04d}"
        result = await allocator.allocate(
            db_session, issue=issue, candidates=three_candidates,
            enabled_devices=three_enabled_devices,
        )
        first_counts[result.device_order[0]] += 1
        digests.append(result.manifest_sha256)
    assert max(first_counts.values()) - min(first_counts.values()) <= 1
    await db_session.rollback()
    assert await replay_assignment_digests(allocator, 300) == digests
```

```python
@pytest.mark.parametrize("device_count,normal,double", [
    (1, 1, 1), (3, 1, 2), (4, 2, 3), (9, 2, 3), (10, 2, 3), (100, 20, 35),
])
def test_exact_direction_caps(device_count, normal, double):
    assert allocation_caps(device_count, double_champion=False) == normal
    assert allocation_caps(device_count, double_champion=True) == double
```

Add tests for: two distinct `FORMAL` champions confirming the same exact direction; candidate-level not qualifying as double confirmation; no alternative signal means skip; pairwise identical executed directions stop at three; shared skips neither increment nor reset the pair count; device online/offline replay; and a submitted device never receives a replacement item.

- [ ] **Step 2: Run allocator tests and verify RED**

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_device_allocator.py -q
```

Expected: collection FAIL because the frozen-signal mappings, assignment models and allocator do not exist.

- [ ] **Step 3: Map Plan 01 frozen candidates without taking schema ownership**

`models/signals.py` maps the already-existing Plan 01 tables exactly and never defines them in Alembic:

```python
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, CHAR, DateTime, Integer, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from champion_follow_server.db.base import Base


class AnonymousActor(Base):
    __tablename__ = "anonymous_actors"
    __table_args__ = {"info": {"schema_owner": "plan01", "read_only": True}}
    namespace_id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_key: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    display_no: Mapped[int] = mapped_column(BigInteger, unique=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AsOfCandidate(Base):
    __tablename__ = "asof_candidates"
    __table_args__ = {"info": {"schema_owner": "plan01", "read_only": True}}
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
    profile_conservative_win_rate: Mapped[Decimal] = mapped_column(Numeric(18, 12))
    profile_conservative_unit_return: Mapped[Decimal] = mapped_column(Numeric(18, 12))
    base_rank: Mapped[int] = mapped_column(Integer)
    statistics_version: Mapped[str] = mapped_column(String(64))
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    unit_profit_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

After creating `signals.py` and `assignments.py`, import both modules from `models/__init__.py` so runtime mapper and relationship resolution is deterministic; the handwritten Alembic revision still must not claim ownership of the read-only Plan 01 tables.

Add this ownership/shape test to `test_device_allocator.py`; the migration integration test from Task 2 remains responsible for database type/nullability checks:

```python
from sqlalchemy import inspect

from champion_follow_server.models.signals import AnonymousActor, AsOfCandidate


def test_plan01_candidate_mapping_is_explicit_and_read_only() -> None:
    assert set(inspect(AsOfCandidate).columns.keys()) == {
        "id", "namespace_id", "snapshot_id", "issue", "market", "actor_key", "direction",
        "signal_source_ms", "lead_ms", "prior_lead_times_ms", "profile_level",
        "profile_sample_count", "profile_wins", "profile_losses", "profile_raw_win_rate",
        "profile_conservative_win_rate", "profile_conservative_unit_return", "base_rank",
        "statistics_version", "frozen_at", "outcome", "unit_profit_micros", "settled_at",
    }
    assert AsOfCandidate.__table__.info == {"schema_owner": "plan01", "read_only": True}
    assert AnonymousActor.__table__.info == {"schema_owner": "plan01", "read_only": True}
```

The allocator joins `(namespace_id, actor_key)` to `AnonymousActor` only to produce `actor_ref=f"A{display_no:06d}"`; no API, task, assignment manifest or log may contain `actor_key`.

The mapping into the signed `BET` is explicit: `AsOfCandidate.id` becomes `signal_id`; immutable candidate rows use wire `signal_version=1`; `statistics_version` is retained in `DeviceAssignment.candidate_statistics_version`; parse `market` only with `^P([1-5]):(size|parity|prime_composite)$` to obtain the ball and family; the stored Chinese direction maps exactly as `大/小/单/双/质/合` to `BIG/SMALL/ODD/EVEN/PRIME/COMPOSITE`; require that the direction belongs to the parsed family; and Plan 01 lowercase `candidate/formal/core` maps exactly to wire `CANDIDATE/FORMAL/CORE`. Reject an unknown market, mismatched direction, `observed`, or any unknown level rather than guessing or applying a generic case conversion. Never read current `actor_profiles` while allocating an issue.

- [ ] **Step 4: Add immutable allocation-round and assignment mappings**

Create `AssignmentRound(__tablename__="assignment_rounds")`, `DeviceAssignment(__tablename__="device_assignments")`, and `PairSequenceCounter(__tablename__="pair_sequence_counters")` as SQLAlchemy mappings of the tables already created by Task 2. A round stores issue, allocation-seed version, enabled-device-set digest, candidate-snapshot digest, deterministic manifest digest and creation time. Each assignment stores device, `AsOfCandidate.id` as `candidate_id`, `candidate_statistics_version`, computed device-specific followable rate, priority index, exact direction, resulting task ID/revision, and `execution_state` (`PLANNED`, `SUBMITTING`, `CONFIRMED`, `SKIPPED`, `CANCELLED`). Unique constraints are `(round_id, device_id)` and `(device_id, period_id, candidate_id)`.

At allocator startup, read `Settings.allocation_seed_path` once, require exactly 32 bytes, keep it only in process memory, and bind it to `Settings.allocation_seed_version`. The seed value never enters rows, API responses, exceptions or logs; rows retain only its immutable version. Device ordering is:

```python
def device_priority(device, issue, seed, prior_first_counts):
    tie = hmac.new(seed, f"{issue}:{device.public_key_fingerprint.hex()}".encode(), sha256).digest()
    return prior_first_counts[device.id], tie, device.public_key_fingerprint
```

Choosing the lowest prior first-priority count first keeps continuously enabled devices within one first-priority assignment; HMAC and fingerprint make ties deterministic.

- [ ] **Step 5: Implement per-device candidate qualification and ordering**

For every enabled device, load its effective threshold and historical safe-lead value. Compute followable rate from the frozen candidate's `prior_lead_times_ms`; never use later samples. Candidate order is:

```text
conservative unit return DESC
computed device followable rate DESC
frozen sample count DESC
internal actor_key ASC
signal_id ASC
```

Reject candidates below effective level/win-rate/followable thresholds, after the safe submission cutoff, from an incomplete issue, or invalidated by collector heartbeat. The emitted task carries only opaque `actor_ref` plus frozen metrics; it never carries internal `actor_key`.

- [ ] **Step 6: Enforce direction and pair-sequence caps**

`allocation_caps(N, double_champion)` returns the exact approved table: `1/2` for N=1..3 (bounded by N), `2/3` for N=4..9, and `max(1,floor(.20N)) / max(normal,floor(.35N))` for N>=10. Double confirmation requires two distinct actor keys, both at least `FORMAL`, independently qualifying for that device and exact `(ball,direction)`.

Before accepting a candidate, simulate every affected device pair's last executed direction counter. Reject a choice that would create a fourth identical executed direction; try the next qualified candidate, otherwise mark that device skipped. A period in which both devices skip does not change the stored counter.

- [ ] **Step 7: Publish revisions only after persisting the round**

In one transaction, lock the issue allocation round, save the online/enabled set and assignments, call `DeviceTaskRevisionService.publish_bet()` for assigned devices and higher `CANCEL(device_reassigned)` for invalidated unsubmitted assignments, then commit before notifying `TaskHub`. If a device already reports `SUBMITTING` or `CONFIRMED`, retain its assignment and do not assign a second candidate. Re-running identical inputs returns the stored manifest and creates no new revisions.

- [ ] **Step 8: Run allocator tests and 300-period deterministic replay**

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_device_allocator.py -q
```

Expected: all tests PASS; the same seed/version/input produces the same 300 manifest hashes; first-priority counts differ by at most one; caps and three-sequence limits hold.

- [ ] **Step 9: Commit allocator**

```bash
git add apps/champion_follow_platform/server/src/champion_follow_server/models/assignments.py \
  apps/champion_follow_platform/server/src/champion_follow_server/models/signals.py \
  apps/champion_follow_platform/server/src/champion_follow_server/services/device_allocator.py \
  apps/champion_follow_platform/server/tests/test_device_allocator.py
git commit -m "feat: allocate champion signals across devices"
```

---

### Task 11: Stream only the current signed task to each authenticated device

**Files:**
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/api/device_ws.py`
- Modify: `apps/champion_follow_platform/server/src/champion_follow_server/app.py`
- Test: `apps/champion_follow_platform/server/tests/test_device_task_websocket.py`

- [ ] **Step 1: Write failing reconnect and isolation WebSocket tests**

```python
# tests/test_device_task_websocket.py
def test_reconnect_receives_highest_cancel_not_older_bet(
    client, device_access_token, active_device, committed_bet_then_cancel
) -> None:
    with client.websocket_connect(
        "/ws/v1/device-tasks",
        headers={"Authorization": f"Bearer {device_access_token}"},
    ) as websocket:
        websocket.send_json(
            {"type": "SYNC", "period_id": "2607270001", "known_revision": 1}
        )
        message = websocket.receive_json()
    assert message["type"] == "TASK"
    assert message["task"]["revision"] == 2
    assert message["task"]["action"] == "CANCEL"


def test_device_cannot_request_another_devices_task(
    client, device_access_token, other_device_task
) -> None:
    with client.websocket_connect(
        "/ws/v1/device-tasks",
        headers={"Authorization": f"Bearer {device_access_token}"},
    ) as websocket:
        websocket.send_json(
            {"type": "SYNC", "period_id": other_device_task.period_id, "known_revision": 0}
        )
        assert websocket.receive_json() == {
            "type": "NO_TASK",
            "period_id": other_device_task.period_id,
            "highest_revision": 0,
        }
```

- [ ] **Step 2: Run WebSocket tests and verify route failure**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_device_task_websocket.py -q
```

Expected: FAIL because `/ws/v1/device-tasks` is not registered.

- [ ] **Step 3: Authenticate the socket before acceptance**

Read the bearer header, authenticate it as an active bound-device session, and close with 4401 before subscribing if absent, expired, revoked, disabled, unbound or epoch-mismatched.

- [ ] **Step 4: Require the initial SYNC frame**

The first client message must be:

```json
{"type":"SYNC","period_id":"2607270001","known_revision":1}
```

Reject every other first-frame shape and do not accept any device ID from client input.

- [ ] **Step 5: Synchronize the authoritative PostgreSQL head**

Query using the authenticated device ID only. If server revision is higher, send a `TASK` containing the complete persisted envelope; otherwise send `{"type":"UP_TO_DATE","period_id":"2607270001","highest_revision":2}`. With no row, send `{"type":"NO_TASK","period_id":"2607270001","highest_revision":0}`.

- [ ] **Step 6: Stream committed notifications and heartbeat**

Subscribe to `TaskHub`, reload every notification from PostgreSQL, and send only revisions higher than the last sent revision for that period. Heartbeat every 10 seconds; recheck session/account/device state and close 4401 when invalidated.

- [ ] **Step 7: Add malformed and cross-device tests**

Assert absent auth, malformed first frames, expired sessions and an attempt to name a device ID fail without leaking another device's state.

- [ ] **Step 8: Add stale-queue and live-revocation tests**

Assert an old queued `BET` after revision-2 `CANCEL` is suppressed. Revoke a live session and assert the socket closes without sending another task.

- [ ] **Step 9: Run WebSocket tests**

Run the Step 2 command again.

Expected: all WebSocket tests PASS.

- [ ] **Step 10: Commit the device task channel**

```bash
git add apps/champion_follow_platform/server/src/champion_follow_server/api/device_ws.py \
  apps/champion_follow_platform/server/src/champion_follow_server/app.py \
  apps/champion_follow_platform/server/tests/test_device_task_websocket.py
git commit -m "feat: stream current signed task per device"
```

---

### Task 12: Persist device orders, settlements, balances, bankroll, and latency

**Files:**
- Modify: `apps/champion_follow_platform/server/src/champion_follow_server/models/__init__.py`
- Create: `apps/champion_follow_platform/contracts/client-event-v1.schema.json`
- Create: `apps/champion_follow_platform/contracts/fixtures/client-event-task-received-v1.json`
- Create: `apps/champion_follow_platform/contracts/fixtures/client-event-execution-state-v1.json`
- Create: `apps/champion_follow_platform/contracts/fixtures/client-event-order-confirmed-v1.json`
- Create: `apps/champion_follow_platform/contracts/fixtures/client-event-order-rejected-v1.json`
- Create: `apps/champion_follow_platform/contracts/fixtures/client-event-order-unknown-v1.json`
- Create: `apps/champion_follow_platform/contracts/fixtures/client-event-settlement-confirmed-v1.json`
- Create: `apps/champion_follow_platform/contracts/fixtures/client-event-balance-snapshot-v1.json`
- Create: `apps/champion_follow_platform/contracts/fixtures/client-event-bankroll-state-v1.json`
- Create: `apps/champion_follow_platform/contracts/fixtures/client-event-latency-sample-v1.json`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/models/ledger.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/schemas/device_events.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/services/device_ledger.py`
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/api/device_events.py`
- Create: `apps/champion_follow_platform/server/tests/test_device_ledger.py`
- Create: `apps/champion_follow_platform/server/tests/test_device_events_api.py`
- Modify: `apps/champion_follow_platform/server/src/champion_follow_server/app.py`

- [ ] **Step 1: Write idempotent order and settlement RED tests**

```python
@pytest.mark.asyncio
async def test_one_confirmed_order_per_device_period_and_settlement_is_idempotent(
    db_session, ledger, active_device, confirmed_task
) -> None:
    order = await ledger.confirm_order(
        db_session, device_id=active_device.id, client_seq=7,
        event_id=UUID("00000000-0000-0000-0000-000000000007"),
        task_id=confirmed_task.id, period_id="2607270001", generation="gen-a",
        client_order_id="client-order-a", platform_order_ref="sha256:fixture",
        stake_minor=100, confirmed_at=utc("2026-07-27T04:00:00Z"),
    )
    replay = await ledger.confirm_order(db_session, **same_confirmation_fields())
    assert replay.id == order.id
    with pytest.raises(OrderConflict):
        await ledger.confirm_order(db_session, **changed_amount_same_period())
    first = await ledger.settle(db_session, order_id=order.id, event_id=SETTLEMENT_ID,
                                outcome="WIN", net_pnl_minor=96, settled_at=clock.now())
    second = await ledger.settle(db_session, order_id=order.id, event_id=SETTLEMENT_ID,
                                 outcome="WIN", net_pnl_minor=96, settled_at=clock.now())
    assert second.id == first.id
```

Add tests for explicit rejection, outcome `UNKNOWN` freezing bankroll, settlement-before-confirmation rejection, balance unavailable, unrecognized balance adjustment remaining separate from P/L, client sequence conflict, and stale binding epoch.

- [ ] **Step 2: Run RED**

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_device_ledger.py tests/test_device_events_api.py -q
```

Expected: collection FAIL because ledger models, schemas, and API do not exist.

- [ ] **Step 3: Add the authoritative ledger mappings**

Map the tables created by Task 2 as `DeviceEventCursor(__tablename__="device_event_cursors")`, `DeviceEvent("device_events")`, `Order("orders")`, `Settlement("settlements")`, `BalanceSnapshot("balance_snapshots")`, `BankrollTelemetry("bankroll_telemetry")`, and `LatencySample("latency_samples")`; import `ledger` from `models/__init__.py`; do not add a second migration or call `create_all()`. Use integer minor units. Enforce:

- unique `(device_id, client_seq)` and unique `(device_id, event_id)`;
- unique confirmed `Order(device_id, period_id)`;
- unique `Settlement(order_id)`;
- every order references the exact signed task and revision;
- balance snapshots carry observed balance or explicit unavailable state;
- bankroll telemetry stores base, cap, unrecovered loss, next planned stake, cycle ID/version and frozen reason, but server never recomputes or silently overwrites the client chain;
- latency samples store segment enum and milliseconds only, not private requests.

Order P/L is always derived from `Settlement.net_pnl_minor`. Balance adjustments, deposits, withdrawals, gifts and rebates never update it.

- [ ] **Step 4: Define and pin the signed client-event contract**

`client-event-v1.schema.json` is a strict discriminated union for `TASK_RECEIVED`, `EXECUTION_STATE`, `ORDER_CONFIRMED`, `ORDER_REJECTED`, `ORDER_UNKNOWN`, `SETTLEMENT_CONFIRMED`, `BALANCE_SNAPSHOT`, `BANKROLL_STATE`, and `LATENCY_SAMPLE`. Every envelope contains schema version, device ID, binding epoch, monotonically increasing client sequence, event UUID, observed UTC timestamp, payload, and ECDSA P-256 signature over canonical JSON excluding only `signature`.

Use these exact wire fields; Python DTOs and the shared JSON Schema must reject aliases and extra keys:

```ts
type ClientEventBase = {
  schema_version: "client-event-v1";
  device_id: string;          // UUID
  binding_epoch: number;      // integer >= 1
  client_seq: number;         // integer >= 1
  event_id: string;           // UUID
  observed_at: string;        // RFC 3339 UTC date-time
  signature: string;          // padded standard Base64 ASN.1 DER ECDSA signature
};

type ClientEvent = ClientEventBase & (
  | {type: "TASK_RECEIVED"; payload: {
      task_id: string; period_id: string; revision: number;
    }}
  | {type: "EXECUTION_STATE"; payload: {
      task_id: string; period_id: string; revision: number; state: "SUBMITTING";
    }}
  | {type: "ORDER_CONFIRMED"; payload: {
      task_id: string; period_id: string; task_revision: number;
      generation: string; client_order_id: string;
      platform_order_ref: string; stake_minor: number; confirmed_at: string;
    }}
  | {type: "ORDER_REJECTED"; payload: {
      task_id: string; period_id: string; task_revision: number;
      generation: string; client_order_id: string;
      reason_code: string; rejected_at: string;
    }}
  | {type: "ORDER_UNKNOWN"; payload: {
      task_id: string; period_id: string; task_revision: number;
      generation: string; client_order_id: string;
      reason_code: string; unknown_at: string;
    }}
  | {type: "SETTLEMENT_CONFIRMED"; payload: {
      client_order_id: string; period_id: string;
      outcome: "WIN" | "LOSS" | "PUSH";
      net_pnl_minor: number; settled_at: string;
    }}
  | {type: "BALANCE_SNAPSHOT"; payload:
      | {availability: "AVAILABLE"; balance_minor: number}
      | {availability: "UNAVAILABLE"; balance_minor: null}
    }
  | {type: "BANKROLL_STATE"; payload: {
      base_minor: number; cap_minor: number; unrecovered_loss_minor: number;
      next_stake_minor: number; cycle_id: string; cycle_version: number;
      frozen_reason: null | "UNKNOWN_SETTLEMENT" | "BALANCE_INSUFFICIENT" | "EVENT_SYNC_CONFLICT";
    }}
  | {type: "LATENCY_SAMPLE"; payload: {
      segment: "TASK_TO_CLIENT" | "SCHEDULER_TO_SUBMIT" | "SUBMIT_TO_CONFIRM";
      milliseconds: number; task_id: string | null;
    }}
);
```

The JSON Schema pins every UUID/date-time format, `period_id` length `1..64`, every revision/cycle/stake/cap/count to an integer in its stated positive or non-negative domain, `reason_code` to `^[A-Z0-9_]{1,64}$`, `platform_order_ref` to `^sha256:[0-9a-f]{64}$`, and `signature` to padded standard Base64. After Base64 decoding, the API independently requires an ASN.1 DER ECDSA signature of 64–80 bytes and verifies it with the bound P-256 SPKI key. `generation`, `client_order_id`, `cycle_id`, and every task/order/event identifier are UUIDs. `net_pnl_minor` is the only signed monetary integer; all other monetary integers are non-negative, a confirmed stake is strictly positive, and every monetary value is bounded to JavaScript's exact integer range `[-9007199254740991, 9007199254740991]` before conversion from local `bigint`.

The contract permits only a locally generated client order ID and an opaque one-way `platform_order_ref`; it forbids Cookie, Token, raw platform response/request, account ID and platform session fields. Save one sanitized fixture per discriminator and validate both Python DTOs and the shared JSON schema.

- [ ] **Step 5: Verify device proof, ACK only committed sequences, and expose sync**

`POST /v1/device/events` authenticates the device access session, checks binding epoch, verifies the event ECDSA signature with the bound SPKI key, row-locks the cursor, accepts only the next sequence or an exact replay, commits the event and projection, then returns `{"ack_seq": n}`. A changed digest at an old sequence returns a generic 409.

`GET /v1/device/sync` returns only that device's current highest task, last order state, unresolved/settled result, latest bankroll telemetry, latest observed balance, global-stop state and server time. It never returns another device or a private platform reference.

- [ ] **Step 6: Wire allocator execution states**

`EXECUTION_STATE=SUBMITTING` locks the assignment against replacement. `ORDER_CONFIRMED` marks it confirmed; `ORDER_REJECTED` marks it rejected without advancing bankroll; `ORDER_UNKNOWN` marks it unknown and blocks further executable tasks until a later exact confirmation/rejection. `SETTLEMENT_CONFIRMED` alone makes the order eligible to advance the client bankroll and reports.

- [ ] **Step 7: Run ledger, API, and privacy tests**

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_device_ledger.py tests/test_device_events_api.py \
  tests/test_security_privacy_scan.py -q
```

Expected: all tests PASS; concurrent duplicate confirmation yields one order; API/log/database scans contain no credentials or raw platform payload.

- [ ] **Step 8: Commit device ledger**

```bash
git add apps/champion_follow_platform/contracts/client-event-v1.schema.json \
  apps/champion_follow_platform/contracts/fixtures/client-event-*-v1.json \
  apps/champion_follow_platform/server/src/champion_follow_server/models/ledger.py \
  apps/champion_follow_platform/server/src/champion_follow_server/schemas/device_events.py \
  apps/champion_follow_platform/server/src/champion_follow_server/services/device_ledger.py \
  apps/champion_follow_platform/server/src/champion_follow_server/api/device_events.py \
  apps/champion_follow_platform/server/src/champion_follow_server/app.py \
  apps/champion_follow_platform/server/tests/test_device_ledger.py \
  apps/champion_follow_platform/server/tests/test_device_events_api.py
git commit -m "feat: record device order and settlement ledger"
```

---

### Task 13: Build Shanghai-period administrator and user reports

**Files:**
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/services/reports.py`
- Create: `apps/champion_follow_platform/server/tests/test_admin_reports.py`

- [ ] **Step 1: Write failing period-boundary tests**

```python
# tests/test_admin_reports.py
from datetime import UTC, datetime

import pytest

from champion_follow_server.services.reports import ReportService, shanghai_periods


def test_shanghai_periods_use_monday_and_calendar_quarter() -> None:
    now = datetime(2026, 7, 27, 1, 30, tzinfo=UTC)  # 09:30 Monday in Shanghai
    periods = shanghai_periods(now)
    assert periods["today"].start == datetime(2026, 7, 26, 16, 0, tzinfo=UTC)
    assert periods["week"].start == datetime(2026, 7, 26, 16, 0, tzinfo=UTC)
    assert periods["quarter"].start == datetime(2026, 6, 30, 16, 0, tzinfo=UTC)
    assert periods["year"].start == datetime(2025, 12, 31, 16, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_report_uses_settlements_for_pnl_and_latest_balance_only(
    db_session, user_with_report_rows, clock
) -> None:
    report = await ReportService().for_account(
        db_session, account_id=user_with_report_rows.account_id, now=clock.now()
    )
    assert report.current_balance_minor == 12_345
    assert report.periods["today"].turnover_minor == 300
    assert report.periods["today"].net_pnl_minor == -4
    assert report.periods["today"].settled_bet_count == 2
    assert report.unrecognized_balance_adjustment_minor == 500
```

- [ ] **Step 2: Run report tests and verify failure**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_admin_reports.py -q
```

Expected: collection FAIL because `services.reports` is absent.

- [ ] **Step 3: Implement exact Asia/Shanghai bounds**

Create an immutable `UtcRange(start, end)` and use `ZoneInfo("Asia/Shanghai")` to calculate today, yesterday, week starting Monday, calendar month, calendar quarter, calendar year and cumulative. Convert boundaries back to UTC before querying. Never derive a period with a fixed `timedelta(hours=8)`.

- [ ] **Step 4: Query latest observed balance**

Select the latest `BalanceSnapshot` at or before `now`. Return its current balance and explicitly recorded unrecognized adjustment; with no snapshot, return `null` for both rather than calculating a balance.

- [ ] **Step 5: Query one settlement-authoritative period**

Join confirmed `Order` rows to `Settlement`, restrict `Order.confirmed_at` to the UTC range, sum `Order.stake_minor` as turnover, sum `Settlement.net_pnl_minor` as P/L, and count settled orders. Thus a late-arriving settlement updates the report for the original bet period instead of shifting that bet into the day when reconciliation completed. Use integer minor units.

- [ ] **Step 6: Compose all approved periods**

Call the one-period query for today, yesterday, week, month, quarter, year and cumulative, returning one immutable account report. Reuse the same query grouped by account for admin overview rather than loading orders into Python.

Admin overview aggregates the same values across accounts. User detail also includes current device state, active threshold version, current unrecovered-loss field from plan 04's amount-chain state when available, last task, last order and execution latency percentiles. Missing optional telemetry is returned as `null`, not zero.

- [ ] **Step 7: Test reporting privacy and pagination**

Add tests that an ordinary user repository query is always scoped to its own account, admin user lists use stable `(created_at, id)` cursor pagination, and report DTOs contain no password hashes, session digests, public keys, raw champion actor keys or platform session fields.

- [ ] **Step 8: Run report tests**

Run the Step 2 command again.

Expected: all report tests PASS.

- [ ] **Step 9: Commit reporting**

```bash
git add apps/champion_follow_platform/server/src/champion_follow_server/services/reports.py \
  apps/champion_follow_platform/server/tests/test_admin_reports.py
git commit -m "feat: report settlement pnl by shanghai periods"
```

---

### Task 14: Expose sole-admin controls, reports, audit, and global stop APIs

**Files:**
- Create: `apps/champion_follow_platform/server/src/champion_follow_server/api/admin.py`
- Modify: `apps/champion_follow_platform/server/src/champion_follow_server/app.py`
- Test: `apps/champion_follow_platform/server/tests/test_admin_api.py`

- [ ] **Step 1: Write failing authorization and global-stop tests**

```python
# tests/test_admin_api.py
def test_user_cannot_read_admin_overview(client, user_access_token) -> None:
    response = client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": f"Bearer {user_access_token}"},
    )
    assert response.status_code == 403


def test_admin_global_stop_cancels_every_live_bet(
    client, admin_headers, two_live_device_bets
) -> None:
    response = client.post(
        "/api/v1/admin/global-stop",
        json={"enabled": True, "reason": "operator safety stop"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert all(head.action == "CANCEL" for head in load_heads(two_live_device_bets))
```

- [ ] **Step 2: Run admin API tests and verify route failure**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_admin_api.py -q
```

Expected: FAIL because admin routes are absent.

- [ ] **Step 3: Add overview and user-report routes**

Implement:

```text
GET /api/v1/admin/overview
GET /api/v1/admin/users
GET /api/v1/admin/users/{account_id}
GET /api/v1/admin/users/{account_id}/report
GET /api/v1/me/report
```

The four administrator routes require `require_admin_context`; `/me/report` always binds account ID from `require_user_context` and accepts no account ID query parameter.

- [ ] **Step 4: Add champion, task, and audit read routes**

Implement stable cursor pagination for:

```text
GET /api/v1/admin/champions
GET /api/v1/admin/tasks
GET /api/v1/admin/audit
```

The champion endpoint exposes only short opaque `actor_ref`, market metrics, grade and signal state. It never returns `actor_key`, source payload or platform identifiers.

- [ ] **Step 5: Add authorization-code and account/device controls**

Implement:

```text
POST   /api/v1/admin/authorization-codes
POST   /api/v1/admin/devices/{device_id}/unbind
POST   /api/v1/admin/accounts/{account_id}/disable
```

The code endpoint returns plaintext exactly once with `Cache-Control: no-store`; audit only ID, purpose and expiry. Unbind/disable revoke sessions and commit signed `CANCEL` revisions before returning.

- [ ] **Step 6: Add threshold mutation routes**

Implement:

```text
POST   /api/v1/admin/thresholds/preview
POST   /api/v1/admin/thresholds
DELETE /api/v1/admin/devices/{device_id}/threshold-override
```

- [ ] **Step 7: Add global-stop mutation route**

Implement:

```text
POST   /api/v1/admin/global-stop
```

Every mutation requires sole-admin access, trusted Origin, CSRF header and a non-empty reason. Global stop uses a row lock, changes a monotonic control version, cancels all live `BET` heads, commits, and only then broadcasts task IDs.

- [ ] **Step 8: Add effective threshold and authorization-code tests**

Assert preview must precede activation, a device override affects only that device, removing it falls back to global, the one-time authorization code is absent from all later GET responses, and every mutation has exactly one immutable audit row with old/new public state and reason.

- [ ] **Step 9: Run admin API tests**

Run the Step 2 command again.

Expected: all admin API tests PASS.

- [ ] **Step 10: Commit administrator API**

```bash
git add apps/champion_follow_platform/server/src/champion_follow_server/api/admin.py \
  apps/champion_follow_platform/server/src/champion_follow_server/app.py \
  apps/champion_follow_platform/server/tests/test_admin_api.py
git commit -m "feat: add sole administrator controls and reports"
```

---

### Task 15: Build the responsive administrator console

**Files:**
- Create: `apps/champion_follow_platform/server/static/admin/index.html`
- Create: `apps/champion_follow_platform/server/static/admin/app.js`
- Create: `apps/champion_follow_platform/server/static/admin/style.css`
- Modify: `apps/champion_follow_platform/server/src/champion_follow_server/app.py`
- Test: `apps/champion_follow_platform/server/tests/test_admin_static.py`

- [ ] **Step 1: Write failing static-console contract tests**

```python
# tests/test_admin_static.py
def test_admin_console_has_required_panels(client) -> None:
    response = client.get("/admin/")
    assert response.status_code == 200
    html = response.text
    for marker in (
        'id="login-panel"',
        'id="overview-panel"',
        'id="users-panel"',
        'id="threshold-panel"',
        'id="authorization-panel"',
        'id="audit-panel"',
        'id="global-stop"',
    ):
        assert marker in html


def test_admin_assets_do_not_use_local_storage_for_tokens(client) -> None:
    script = client.get("/admin/app.js").text
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "innerHTML" not in script
```

- [ ] **Step 2: Run static tests and verify 404**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_admin_static.py -q
```

Expected: FAIL because `/admin/` returns 404.

- [ ] **Step 3: Create login and overview markup**

Add the username/password/TOTP login form, server/collector/device health cards, total balance, today turnover, today P/L, cumulative P/L and global-stop confirmation with required reason.

- [ ] **Step 4: Create user and champion markup**

Add the paginated user table with today/yesterday/week/month/quarter/year/cumulative columns, selected-user balance/orders/settlements/device/threshold/latency detail, and champion table.

- [ ] **Step 5: Create threshold, authorization, and audit markup**

Add the threshold editor with separate Preview/Activate buttons, 7/30-day result table and required reason; one-time authorization-code dialog; and append-only audit table.

Use `<template>` plus `document.createElement()` and `textContent`; never interpolate server data into HTML.

- [ ] **Step 6: Implement in-memory login and refresh client**

In `app.js`, keep `accessToken` and `csrfToken` in module variables only. Login stores them in memory. `api()` adds authorization/CSRF, retries once through `/api/v1/admin/session/refresh` on 401, and resets to login if refresh fails. Use `credentials: "same-origin"`, `cache: "no-store"`, and require JSON content type.

- [ ] **Step 7: Implement dashboard loading and safe rendering**

Fetch overview, users, selected-user detail, champions and audit through `api()`. Render with `document.createElement()` and `textContent`. Clear one-time code text and its variable when the dialog closes.

- [ ] **Step 8: Implement threshold and safety mutations**

Wire Preview before Activate, authorization-code generation, device unbind, account disable and global stop. Disable submit controls during requests and render generic failure text without response bodies containing credentials.

- [ ] **Step 9: Add responsive, accessible styling**

Use a two-column desktop layout above 960 px and one-column layout below it. P/L uses both text and color (`盈利 +123.45`, `亏损 -12.34`); status badges include visible labels; tables have sticky headers and horizontal scrolling. Inputs have explicit labels, keyboard focus outlines and 44 px minimum target height.

- [ ] **Step 10: Mount static files without directory listing**

Mount only `/admin/index.html`, `/admin/app.js`, and `/admin/style.css`. Set `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self' wss:` plus `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and `Cache-Control: no-store` on HTML.

- [ ] **Step 11: Run static tests**

Run the Step 2 command again.

Expected: `2 passed`.

- [ ] **Step 12: Commit administrator console**

```bash
git add apps/champion_follow_platform/server/static/admin \
  apps/champion_follow_platform/server/src/champion_follow_server/app.py \
  apps/champion_follow_platform/server/tests/test_admin_static.py
git commit -m "feat: add responsive champion admin console"
```

---

### Task 16: Run security, privacy, concurrency, and end-to-end acceptance

**Files:**
- Create: `apps/champion_follow_platform/server/tests/test_security_privacy_scan.py`
- Create: `apps/champion_follow_platform/server/tests/test_auth_admin_e2e.py`

- [ ] **Step 1: Add a failing serialized-artifact privacy scan**

```python
# tests/test_security_privacy_scan.py
from champion_follow_server.schemas.admin import (
    AuditPage,
    ChampionPage,
    OverviewResponse,
    TaskPage,
    ThresholdConfigResponse,
    ThresholdPreviewResponse,
    UserReportResponse,
)
from champion_follow_server.schemas.auth import (
    AdminSessionResponse,
    EnrollmentResponse,
    UserSessionResponse,
)

FORBIDDEN_KEYS = {
    "password_hash",
    "access_digest",
    "refresh_digest",
    "csrf_digest",
    "secret_ciphertext",
    "public_key_spki_der",
    "actor_key",
    "platform_cookie",
    "platform_token",
}


def walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def test_every_documented_api_response_schema_excludes_private_fields() -> None:
    response_models = (
        AdminSessionResponse,
        EnrollmentResponse,
        UserSessionResponse,
        AuditPage,
        ChampionPage,
        OverviewResponse,
        TaskPage,
        ThresholdConfigResponse,
        ThresholdPreviewResponse,
        UserReportResponse,
    )
    seen = {
        key
        for model in response_models
        for key in walk(model.model_json_schema(mode="serialization"))
    }
    assert seen.isdisjoint(FORBIDDEN_KEYS)
```

- [ ] **Step 2: Add one complete end-to-end test**

The test performs this exact sequence with fake credentials and keys:

1. bootstrap and confirm the sole admin;
2. admin login with TOTP;
3. issue a REGISTER code;
4. register a user/device with valid ECDSA P-256 proof;
5. activate a global threshold only after preview;
6. issue signed `BET` revision 1;
7. connect the device WebSocket and synchronize revision 1;
8. enable global stop;
9. receive signed `CANCEL` revision 2;
10. verify reconnect still returns revision 2;
11. create settled orders and balance snapshots around Shanghai day/week boundaries;
12. verify admin and own-user reports, ordinary-user denial from admin endpoints, and one audit row per mutation.

- [ ] **Step 3: Run the new tests and fix only exposed defects**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_security_privacy_scan.py tests/test_auth_admin_e2e.py -q
```

Expected: all tests PASS. If a failure exposes a defect, add the smallest regression assertion to the owning test file, make the minimal production change, and rerun this command.

- [ ] **Step 4: Run the complete server suite**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest -q
```

Expected: all plans 01–03 server tests PASS with no warnings about un-awaited coroutines, leaked connections or unknown markers.

- [ ] **Step 5: Run migration round-trip in a disposable PostgreSQL database**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml exec -T postgres \
  psql -U app -d postgres -v ON_ERROR_STOP=1 \
  -c 'DROP DATABASE IF EXISTS champion_migration_test' \
  -c 'CREATE DATABASE champion_migration_test'
docker compose -f apps/champion_follow_platform/compose.yaml run --rm \
  -e CHAMPION_DATABASE_URL=postgresql+asyncpg://app:app@postgres/champion_migration_test \
  server sh -lc 'alembic upgrade head && alembic downgrade base && alembic upgrade head'
```

Expected: all three Alembic operations exit 0; the immutable-audit trigger is recreated after the final upgrade.

- [ ] **Step 6: Run a focused 100-device task isolation test**

Run:

```bash
docker compose -f apps/champion_follow_platform/compose.yaml run --rm server \
  python -m pytest tests/test_device_task_websocket.py -k hundred_devices -q
```

Expected: 100 authenticated fake devices receive only their own highest revision; there are no duplicate `(device_id, period_id, revision)` rows and no cross-device payloads.

- [ ] **Step 7: Scan tracked implementation files for secret-bearing debug output**

Run:

```bash
if git grep -nE 'print\(.*(password|token|secret|cookie|private_key|authorization_code)|logger\..*(password|token|secret|cookie|private_key|authorization_code)' \
  -- apps/champion_follow_platform/server/src apps/champion_follow_platform/server/static; then
  echo 'secret-bearing debug statement found' >&2
  exit 1
else
  echo 'privacy grep: clean'
fi
```

Expected: `privacy grep: clean` and exit status 0. Any reported match must be removed or changed to a non-sensitive identifier before proceeding.

- [ ] **Step 8: Commit acceptance coverage**

```bash
git add apps/champion_follow_platform/server/tests/test_security_privacy_scan.py \
  apps/champion_follow_platform/server/tests/test_auth_admin_e2e.py
git commit -m "test: verify champion auth and admin boundaries"
```

---

## Completion checklist

- [ ] A one-time code cannot be reused concurrently and its plaintext is shown only once.
- [ ] Registration and rebind require a valid ASN.1 DER ECDSA-SHA256 proof from the submitted CNG ECDSA P-256 SPKI DER public key; no raw/private device key is accepted or stored.
- [ ] Ed25519 is used only for server task signing, and every client event is instead verified against the bound ECDSA P-256 device key.
- [ ] Exactly one TOTP-confirmed administrator can exist.
- [ ] Unbind, disable, logout and refresh-token reuse revoke the intended sessions immediately.
- [ ] No live `BET` exists without an explicit active threshold version created from a matching 7/30-day `as-of` preview.
- [ ] Plan 03 persists admin approvals only in `admin_threshold_previews`; Alembic never creates, alters or drops Plan 01 `threshold_previews`, `anonymous_actors`, `asof_candidates` or any other Plan 01-owned table.
- [ ] Every state invalidation commits a higher signed `CANCEL` revision.
- [ ] Reconnect returns the PostgreSQL head, so an old queued `BET` cannot revive after `CANCEL`.
- [ ] Every `BET` is derived from a frozen Plan 01 `asof_candidates` row, contains no stake amount, and exposes only an `A000007`-style actor reference.
- [ ] Deterministic allocation satisfies the 1–3/4–9/10+ device caps, balanced seeded rotation, double-champion qualification, and the maximum-three identical executed-direction rule under replay.
- [ ] Signed device-event ingestion is sequence-idempotent and the device ledger preserves exact order, settlement, balance, bankroll and latency history without platform credentials or raw payloads.
- [ ] Admin reports use settled orders for P/L, latest observed balance for balance, and Asia/Shanghai calendar boundaries.
- [ ] The administrator sees every user's balance and day/week/month/quarter/year/cumulative P/L; ordinary users see only themselves.
- [ ] Every admin mutation has a same-transaction append-only audit row with no secret material.
- [ ] Full tests, migration round-trip, 100-device isolation and privacy scans pass.

Plan 03 is complete when these checks pass; real platform execution remains disabled until plan 04's Windows Electron client passes the separately required Windows 1-unit closed-loop verification.
