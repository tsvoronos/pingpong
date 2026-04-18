"""Add user_connectors table

Revision ID: 337b7d1fe811
Revises: b4f9d7e21c5a
Create Date: 2026-04-18 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "337b7d1fe811"
down_revision: Union[str, None] = "b4f9d7e21c5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _ = revision, down_revision, branch_labels, depends_on

    op.create_table(
        "user_connectors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("service", sa.String(), nullable=False),
        sa.Column("tenant", sa.String(), nullable=True),
        sa.Column("access_token", sa.String(), nullable=False),
        sa.Column("refresh_token", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.String(), nullable=True),
        sa.Column("external_user_id", sa.String(), nullable=True),
        sa.Column(
            "created",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        # NB: no ondelete="CASCADE" — user deletion must revoke through the
        # connector before dropping rows, otherwise the upstream tokens stay
        # valid on the provider side.
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "service", "tenant", name="uq_user_service_tenant"
        ),
    )
    op.create_index(
        "idx_user_connectors_user_id",
        "user_connectors",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_user_connectors_user_id", table_name="user_connectors")
    op.drop_table("user_connectors")
