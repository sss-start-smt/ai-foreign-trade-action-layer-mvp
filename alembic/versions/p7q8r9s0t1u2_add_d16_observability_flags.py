"""add D16 observability / feature flags

Revision ID: p7q8r9s0t1u2
Revises: o6p7q8r9s0t1
"""
from __future__ import annotations

from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "p7q8r9s0t1u2"
down_revision: Union[str, None] = "o6p7q8r9s0t1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "d16_feature_flag_overrides",
        sa.Column("override_id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("flag_key", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("rollout_percent", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("reason", sa.Text()),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("scope_type", "scope_id", "flag_key", name="uq_d16_flag_scope"),
    )
    op.create_index("idx_d16_flags_org_key", "d16_feature_flag_overrides", ["organization_id", "flag_key", "scope_type", "scope_id"])
    op.create_table(
        "d16_feature_flag_events",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("flag_key", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("old_value_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("new_value_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_d16_flag_events_org_time", "d16_feature_flag_events", ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_d16_flag_events_org_time", table_name="d16_feature_flag_events")
    op.drop_table("d16_feature_flag_events")
    op.drop_index("idx_d16_flags_org_key", table_name="d16_feature_flag_overrides")
    op.drop_table("d16_feature_flag_overrides")
