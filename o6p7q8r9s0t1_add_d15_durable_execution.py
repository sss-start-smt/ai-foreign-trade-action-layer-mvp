"""add D15 durable execution / RESULT_UNCERTAIN state

Revision ID: o6p7q8r9s0t1
Revises: n5o6p7q8r9s0
"""
from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "o6p7q8r9s0t1"
down_revision: Union[str, None] = "n5o6p7q8r9s0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "d15_outbox_execution_state",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("business_action_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("retry_budget", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dispatch_started", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_known", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("external_effect_status", sa.Text(), nullable=False, server_default="UNKNOWN"),
        sa.Column("error_kind", sa.Text()),
        sa.Column("user_message_code", sa.Text(), nullable=False, server_default="ACTION_PENDING"),
        sa.Column("reconciliation_status", sa.Text()),
        sa.Column("next_attempt_at", sa.Text()),
        sa.Column("last_attempt_at", sa.Text()),
        sa.Column("resolved_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["d10_outbox_events.event_id"]),
        sa.ForeignKeyConstraint(["business_action_id"], ["d10_business_actions.business_action_id"]),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_d15_execution_idempotency"),
    )
    op.create_index(
        "idx_d15_execution_state_queue",
        "d15_outbox_execution_state",
        ["organization_id", "state", "next_attempt_at", "updated_at"],
    )
    op.create_table(
        "d15_execution_trace_events",
        sa.Column("trace_id", sa.Text(), primary_key=True),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("error_kind", sa.Text()),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dispatch_started", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_known", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("external_effect_status", sa.Text(), nullable=False, server_default="UNKNOWN"),
        sa.Column("response_meta_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("actor", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["d10_outbox_events.event_id"]),
        sa.UniqueConstraint("event_id", "sequence_no", name="uq_d15_trace_event_seq"),
    )
    op.create_index("idx_d15_trace_event_seq", "d15_execution_trace_events", ["event_id", "sequence_no"])


def downgrade() -> None:
    op.drop_index("idx_d15_trace_event_seq", table_name="d15_execution_trace_events")
    op.drop_table("d15_execution_trace_events")
    op.drop_index("idx_d15_execution_state_queue", table_name="d15_outbox_execution_state")
    op.drop_table("d15_outbox_execution_state")
