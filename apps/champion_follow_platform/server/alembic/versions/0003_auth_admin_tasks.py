"""Add authorization, administration, assignment, and ledger state."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003_auth_admin_tasks"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UUID = postgresql.UUID(as_uuid=True)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "app_accounts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("username_canonical", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(8), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("admin_slot", sa.Integer(), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("username_canonical"),
        sa.UniqueConstraint("admin_slot"),
        sa.CheckConstraint(
            "(role = 'ADMIN' AND admin_slot = 1) OR "
            "(role = 'USER' AND admin_slot IS NULL)",
            name="ck_admin_slot_matches_role",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACTIVE', 'DISABLED')",
            name="ck_app_accounts_status",
        ),
        sa.CheckConstraint(
            "failed_login_count >= 0", name="ck_failed_login_count_nonnegative"
        ),
    )
    op.create_index(
        "ix_app_accounts_username_canonical",
        "app_accounts",
        ["username_canonical"],
    )

    op.create_table(
        "devices",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "account_id",
            UUID,
            sa.ForeignKey("app_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("public_key_spki_der", sa.LargeBinary(), nullable=False),
        sa.Column("public_key_fingerprint", sa.LargeBinary(32), nullable=False),
        sa.Column("binding_epoch", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("unbound_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("public_key_fingerprint"),
        sa.CheckConstraint("binding_epoch >= 1", name="ck_device_binding_epoch"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'UNBOUND')", name="ck_device_status"
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND unbound_at IS NULL) OR "
            "(status = 'UNBOUND' AND unbound_at IS NOT NULL)",
            name="ck_device_unbound_state",
        ),
    )
    op.create_index("ix_devices_account_id", "devices", ["account_id"])
    op.create_index(
        "uq_devices_one_active_per_account",
        "devices",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "authorization_codes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("digest", sa.LargeBinary(32), nullable=False),
        sa.Column("purpose", sa.String(8), nullable=False),
        sa.Column(
            "target_account_id",
            UUID,
            sa.ForeignKey("app_accounts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consumed_by_device_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_timestamps(),
        sa.UniqueConstraint("digest"),
        sa.CheckConstraint(
            "(purpose = 'REGISTER' AND target_account_id IS NULL) OR "
            "(purpose = 'REBIND' AND target_account_id IS NOT NULL)",
            name="ck_authorization_code_target",
        ),
        sa.CheckConstraint(
            "(consumed_at IS NULL AND consumed_by_device_id IS NULL) OR "
            "consumed_at IS NOT NULL",
            name="ck_authorization_code_consumption",
        ),
    )

    op.create_table(
        "enrollment_challenges",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "authorization_code_id",
            UUID,
            sa.ForeignKey("authorization_codes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nonce", sa.LargeBinary(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "ix_enrollment_challenges_authorization_code_id",
        "enrollment_challenges",
        ["authorization_code_id"],
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "account_id",
            UUID,
            sa.ForeignKey("app_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("binding_epoch", sa.Integer(), nullable=True),
        sa.Column("family_id", UUID, nullable=False),
        sa.Column(
            "rotated_from_id",
            UUID,
            sa.ForeignKey("auth_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("access_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("refresh_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("csrf_digest", sa.LargeBinary(32), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("access_digest"),
        sa.UniqueConstraint("refresh_digest"),
        sa.CheckConstraint(
            "(kind = 'USER' AND device_id IS NOT NULL "
            "AND binding_epoch IS NOT NULL AND csrf_digest IS NULL) OR "
            "(kind = 'ADMIN' AND device_id IS NULL "
            "AND binding_epoch IS NULL AND csrf_digest IS NOT NULL)",
            name="ck_auth_session_kind_binding",
        ),
        sa.CheckConstraint(
            "refresh_expires_at > access_expires_at",
            name="ck_auth_session_expiry_order",
        ),
    )
    op.create_index("ix_auth_sessions_account_id", "auth_sessions", ["account_id"])
    op.create_index("ix_auth_sessions_family_id", "auth_sessions", ["family_id"])

    op.create_table(
        "device_login_challenges",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "account_id",
            UUID,
            sa.ForeignKey("app_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nonce", sa.LargeBinary(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "ix_device_login_challenges_account_id",
        "device_login_challenges",
        ["account_id"],
    )
    op.create_index(
        "ix_device_login_challenges_device_id",
        "device_login_challenges",
        ["device_id"],
    )

    op.create_table(
        "admin_totp",
        sa.Column(
            "account_id",
            UUID,
            sa.ForeignKey("app_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "admin_threshold_previews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "created_by_account_id",
            UUID,
            sa.ForeignKey("app_accounts.id"),
            nullable=False,
        ),
        sa.Column(
            "device_id", UUID, sa.ForeignKey("devices.id"), nullable=True
        ),
        sa.Column("proposal_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("watermark_snapshot_id", UUID, nullable=False),
        sa.Column("windows", sa.JSON(), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "expires_at > viewed_at", name="ck_admin_preview_expiry"
        ),
    )
    op.create_index(
        "ix_admin_threshold_previews_created_by_account_id",
        "admin_threshold_previews",
        ["created_by_account_id"],
    )
    op.create_index(
        "ix_admin_threshold_previews_watermark_snapshot_id",
        "admin_threshold_previews",
        ["watermark_snapshot_id"],
    )

    op.execute("CREATE SEQUENCE threshold_config_version_seq START WITH 1")
    op.create_table(
        "threshold_configs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("config_version", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column(
            "device_id", UUID, sa.ForeignKey("devices.id"), nullable=True
        ),
        sa.Column("minimum_level", sa.String(16), nullable=True),
        sa.Column(
            "minimum_conservative_win_rate",
            sa.Numeric(12, 10),
            nullable=True,
        ),
        sa.Column(
            "minimum_conservative_roi", sa.Numeric(12, 10), nullable=True
        ),
        sa.Column(
            "minimum_followable_rate", sa.Numeric(12, 10), nullable=True
        ),
        sa.Column(
            "effective_minimum_win_rate",
            sa.Numeric(12, 10),
            nullable=True,
        ),
        sa.Column(
            "preview_id",
            UUID,
            sa.ForeignKey("admin_threshold_previews.id"),
            nullable=True,
        ),
        sa.Column("is_removal", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "created_by_account_id",
            UUID,
            sa.ForeignKey("app_accounts.id"),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("config_version"),
        sa.CheckConstraint(
            "(scope = 'GLOBAL' AND scope_key = 'GLOBAL' AND device_id IS NULL) "
            "OR (scope = 'DEVICE' AND scope_key = device_id::text "
            "AND device_id IS NOT NULL)",
            name="ck_threshold_scope_key",
        ),
        sa.CheckConstraint(
            "(is_removal AND scope = 'DEVICE' AND preview_id IS NULL "
            "AND minimum_level IS NULL "
            "AND minimum_conservative_win_rate IS NULL "
            "AND minimum_conservative_roi IS NULL "
            "AND minimum_followable_rate IS NULL "
            "AND effective_minimum_win_rate IS NULL) OR "
            "(NOT is_removal AND preview_id IS NOT NULL "
            "AND minimum_level IS NOT NULL "
            "AND minimum_conservative_win_rate IS NOT NULL "
            "AND minimum_conservative_roi IS NOT NULL "
            "AND minimum_followable_rate IS NOT NULL "
            "AND effective_minimum_win_rate IS NOT NULL)",
            name="ck_threshold_config_shape",
        ),
        sa.CheckConstraint(
            "minimum_conservative_win_rate BETWEEN 0 AND 1",
            name="ck_threshold_win_rate",
        ),
        sa.CheckConstraint(
            "minimum_followable_rate BETWEEN 0 AND 1",
            name="ck_threshold_followable_rate",
        ),
        sa.CheckConstraint(
            "effective_minimum_win_rate BETWEEN 0 AND 1",
            name="ck_threshold_effective_win_rate",
        ),
    )
    op.create_index(
        "ix_threshold_configs_config_version",
        "threshold_configs",
        ["config_version"],
    )
    op.create_index(
        "ix_threshold_configs_scope_key",
        "threshold_configs",
        ["scope_key"],
    )
    op.create_index(
        "uq_threshold_configs_active_scope",
        "threshold_configs",
        ["scope_key"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "global_controls",
        sa.Column("key", sa.String(32), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "updated_by_account_id",
            UUID,
            sa.ForeignKey("app_accounts.id"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_global_control_version"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "actor_account_id",
            UUID,
            sa.ForeignKey("app_accounts.id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(80), nullable=False),
        sa.Column("target_id", sa.String(80), nullable=False),
        sa.Column("old_state", sa.JSON(), nullable=True),
        sa.Column("new_state", sa.JSON(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_audit_events_actor_account_id", "audit_events", ["actor_account_id"]
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index(
        "ix_audit_events_request_id", "audit_events", ["request_id"]
    )
    op.create_index(
        "ix_audit_events_created_at", "audit_events", ["created_at"]
    )
    op.execute(
        """
        CREATE FUNCTION reject_audit_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'audit_events are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
        """
    )

    op.create_table(
        "device_task_revisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "device_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signing_key_version", sa.String(32), nullable=False),
        sa.Column("signature", sa.LargeBinary(64), nullable=False),
        sa.Column("canonical_sha256", sa.LargeBinary(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "device_id", "period_id", "revision", name="uq_task_revision"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_task_revision_positive"),
        sa.CheckConstraint(
            "action IN ('BET', 'CANCEL')", name="ck_task_action"
        ),
        sa.CheckConstraint("expires_at > issued_at", name="ck_task_expiry"),
    )
    op.create_index(
        "ix_device_task_revisions_device_id",
        "device_task_revisions",
        ["device_id"],
    )
    op.create_index(
        "ix_device_task_revisions_period_id",
        "device_task_revisions",
        ["period_id"],
    )

    op.create_table(
        "device_task_heads",
        sa.Column(
            "device_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("period_id", sa.String(64), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "task_id",
            UUID,
            sa.ForeignKey("device_task_revisions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.CheckConstraint("revision >= 1", name="ck_task_head_revision"),
    )

    op.create_table(
        "assignment_rounds",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("period_id", sa.String(64), nullable=False, unique=True),
        sa.Column("allocation_seed_version", sa.String(32), nullable=False),
        sa.Column("enabled_device_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("candidate_snapshot_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("manifest_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "device_assignments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "round_id",
            UUID,
            sa.ForeignKey("assignment_rounds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            UUID,
            sa.ForeignKey("asof_candidates.id"),
            nullable=False,
        ),
        sa.Column("candidate_statistics_version", sa.String(64), nullable=False),
        sa.Column("period_id", sa.String(64), nullable=False),
        sa.Column("followable_rate", sa.Numeric(12, 10), nullable=False),
        sa.Column("priority_index", sa.Integer(), nullable=False),
        sa.Column("ball", sa.SmallInteger(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column(
            "task_id",
            UUID,
            sa.ForeignKey("device_task_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("task_revision", sa.BigInteger(), nullable=True),
        sa.Column("execution_state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "round_id", "device_id", name="uq_assignment_round_device"
        ),
        sa.UniqueConstraint(
            "device_id",
            "period_id",
            "candidate_id",
            name="uq_assignment_device_period_candidate",
        ),
        sa.CheckConstraint("priority_index >= 0", name="ck_assignment_priority"),
        sa.CheckConstraint("ball BETWEEN 1 AND 5", name="ck_assignment_ball"),
        sa.CheckConstraint(
            "followable_rate BETWEEN 0 AND 1",
            name="ck_assignment_followable_rate",
        ),
        sa.CheckConstraint(
            "execution_state IN "
            "('PLANNED', 'SUBMITTING', 'CONFIRMED', 'SKIPPED', 'CANCELLED')",
            name="ck_assignment_execution_state",
        ),
        sa.CheckConstraint(
            "(task_id IS NULL AND task_revision IS NULL) OR "
            "(task_id IS NOT NULL AND task_revision >= 1)",
            name="ck_assignment_task_state",
        ),
    )

    op.create_table(
        "pair_sequence_counters",
        sa.Column(
            "device_a_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "device_b_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("last_ball", sa.SmallInteger(), nullable=True),
        sa.Column("last_direction", sa.String(16), nullable=True),
        sa.Column("identical_count", sa.SmallInteger(), nullable=False),
        sa.Column("last_period_id", sa.String(64), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "device_a_id < device_b_id", name="ck_pair_canonical_order"
        ),
        sa.CheckConstraint(
            "identical_count BETWEEN 0 AND 3", name="ck_pair_identical_count"
        ),
        sa.CheckConstraint(
            "last_ball IS NULL OR last_ball BETWEEN 1 AND 5",
            name="ck_pair_last_ball",
        ),
        sa.CheckConstraint("version >= 1", name="ck_pair_version"),
    )

    op.create_table(
        "device_event_cursors",
        sa.Column(
            "device_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("binding_epoch", sa.Integer(), nullable=False),
        sa.Column("acknowledged_client_seq", sa.BigInteger(), nullable=False),
        sa.Column("last_event_digest", sa.LargeBinary(32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("binding_epoch >= 1", name="ck_event_cursor_epoch"),
        sa.CheckConstraint(
            "acknowledged_client_seq >= 0", name="ck_event_cursor_sequence"
        ),
        sa.CheckConstraint(
            "(acknowledged_client_seq = 0 AND last_event_digest IS NULL) OR "
            "(acknowledged_client_seq > 0 AND last_event_digest IS NOT NULL)",
            name="ck_event_cursor_digest",
        ),
    )

    op.create_table(
        "device_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "device_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("binding_epoch", sa.Integer(), nullable=False),
        sa.Column("client_seq", sa.BigInteger(), nullable=False),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("canonical_payload_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("signature_der", sa.LargeBinary(), nullable=False),
        sa.UniqueConstraint(
            "device_id", "client_seq", name="uq_device_event_sequence"
        ),
        sa.UniqueConstraint(
            "device_id", "event_id", name="uq_device_event_identity"
        ),
        sa.CheckConstraint("binding_epoch >= 1", name="ck_device_event_epoch"),
        sa.CheckConstraint("client_seq >= 1", name="ck_device_event_sequence"),
    )

    op.create_table(
        "orders",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "device_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            UUID,
            sa.ForeignKey("device_task_revisions.id"),
            nullable=False,
        ),
        sa.Column("task_revision", sa.BigInteger(), nullable=False),
        sa.Column("period_id", sa.String(64), nullable=False),
        sa.Column("generation", UUID, nullable=False),
        sa.Column("client_order_id", UUID, nullable=False),
        sa.Column("platform_order_ref", sa.String(71), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stake_minor", sa.BigInteger(), nullable=True),
        sa.Column(
            "confirmation_event_id",
            UUID,
            sa.ForeignKey("device_events.id"),
            nullable=True,
            unique=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "device_id", "client_order_id", name="uq_order_client_id"
        ),
        sa.CheckConstraint(
            "status IN ('CONFIRMED', 'REJECTED', 'UNKNOWN')",
            name="ck_order_status",
        ),
        sa.CheckConstraint(
            "(status = 'CONFIRMED' AND stake_minor > 0 "
            "AND platform_order_ref IS NOT NULL "
            "AND confirmation_event_id IS NOT NULL "
            "AND confirmed_at IS NOT NULL) OR "
            "(status IN ('REJECTED', 'UNKNOWN') AND stake_minor IS NULL "
            "AND platform_order_ref IS NULL AND confirmed_at IS NULL)",
            name="ck_order_confirmation_shape",
        ),
        sa.CheckConstraint("task_revision >= 1", name="ck_order_task_revision"),
    )
    op.create_index(
        "uq_orders_one_confirmed_per_device_period",
        "orders",
        ["device_id", "period_id"],
        unique=True,
        postgresql_where=sa.text("status = 'CONFIRMED'"),
    )

    op.create_table(
        "settlements",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "order_id",
            UUID,
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "event_id",
            UUID,
            sa.ForeignKey("device_events.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("outcome", sa.String(8), nullable=False),
        sa.Column("net_pnl_minor", sa.BigInteger(), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('WIN', 'LOSS', 'PUSH')", name="ck_settlement_outcome"
        ),
    )

    op.create_table(
        "balance_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "event_id",
            UUID,
            sa.ForeignKey("device_events.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "device_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("availability", sa.String(16), nullable=False),
        sa.Column("balance_minor", sa.BigInteger(), nullable=True),
        sa.Column(
            "unrecognized_adjustment_minor", sa.BigInteger(), nullable=True
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(availability = 'AVAILABLE' AND balance_minor IS NOT NULL "
            "AND unrecognized_adjustment_minor IS NOT NULL) OR "
            "(availability = 'UNAVAILABLE' AND balance_minor IS NULL "
            "AND unrecognized_adjustment_minor IS NULL)",
            name="ck_balance_snapshot_availability",
        ),
    )

    op.create_table(
        "bankroll_telemetry",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "event_id",
            UUID,
            sa.ForeignKey("device_events.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "device_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("base_minor", sa.BigInteger(), nullable=False),
        sa.Column("cap_minor", sa.BigInteger(), nullable=False),
        sa.Column("unrecovered_loss_minor", sa.BigInteger(), nullable=False),
        sa.Column("next_stake_minor", sa.BigInteger(), nullable=False),
        sa.Column("cycle_id", UUID, nullable=False),
        sa.Column("cycle_version", sa.BigInteger(), nullable=False),
        sa.Column("frozen_reason", sa.String(32), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "base_minor >= 0 AND cap_minor >= 0 "
            "AND unrecovered_loss_minor >= 0 AND next_stake_minor >= 0",
            name="ck_bankroll_amounts_nonnegative",
        ),
        sa.CheckConstraint(
            "cycle_version >= 1", name="ck_bankroll_cycle_version"
        ),
        sa.CheckConstraint(
            "frozen_reason IS NULL OR frozen_reason IN "
            "('UNKNOWN_SETTLEMENT', 'BALANCE_INSUFFICIENT', "
            "'EVENT_SYNC_CONFLICT')",
            name="ck_bankroll_frozen_reason",
        ),
    )

    op.create_table(
        "latency_samples",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "event_id",
            UUID,
            sa.ForeignKey("device_events.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "device_id",
            UUID,
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            UUID,
            sa.ForeignKey("device_task_revisions.id"),
            nullable=True,
        ),
        sa.Column("segment", sa.String(32), nullable=False),
        sa.Column("milliseconds", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "segment IN "
            "('TASK_TO_CLIENT', 'SCHEDULER_TO_SUBMIT', 'SUBMIT_TO_CONFIRM')",
            name="ck_latency_segment",
        ),
        sa.CheckConstraint(
            "milliseconds >= 0", name="ck_latency_milliseconds_nonnegative"
        ),
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_mutation()")
    op.execute("DROP SEQUENCE IF EXISTS threshold_config_version_seq")

    op.drop_table("latency_samples")
    op.drop_table("bankroll_telemetry")
    op.drop_table("balance_snapshots")
    op.drop_table("settlements")
    op.drop_table("orders")
    op.drop_table("device_events")
    op.drop_table("device_event_cursors")
    op.drop_table("pair_sequence_counters")
    op.drop_table("device_assignments")
    op.drop_table("assignment_rounds")
    op.drop_table("device_task_heads")
    op.drop_table("device_task_revisions")
    op.drop_table("audit_events")
    op.drop_table("global_controls")
    op.drop_table("threshold_configs")
    op.drop_table("admin_threshold_previews")
    op.drop_table("admin_totp")
    op.drop_table("device_login_challenges")
    op.drop_table("auth_sessions")
    op.drop_table("enrollment_challenges")
    op.drop_table("authorization_codes")
    op.drop_table("devices")
    op.drop_table("app_accounts")
