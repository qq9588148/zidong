"""add signed platform endpoint configuration

Revision ID: 0004_platform_endpoint_config
Revises: 0003_auth_admin_tasks
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_platform_endpoint_config"
down_revision = "0003_auth_admin_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_endpoint_configs",
        sa.Column("key", sa.String(length=32), primary_key=True),
        sa.Column("config_version", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("entry_url", sa.String(length=2048), nullable=False),
        sa.Column("allowed_origins", sa.JSON(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signing_key_version", sa.String(length=32), nullable=False),
        sa.Column("signature", sa.LargeBinary(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "updated_by_account_id",
            sa.Uuid(),
            sa.ForeignKey("app_accounts.id"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("platform_endpoint_configs")
