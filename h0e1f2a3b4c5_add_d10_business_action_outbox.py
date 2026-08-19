"""add D10 BusinessAction / Outbox / Idempotency / Audit

Revision ID: h0e1f2a3b4c5
Revises: g9d0e1f2a3b4
Create Date: 2026-08-14 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "h0e1f2a3b4c5"
down_revision: Union[str, None] = "g9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "d10_business_actions",
        sa.Column("business_action_id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("action_case_id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("order_id", sa.String(), nullable=False),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("effect_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'ACCEPTED'")),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["action_case_id"], ["action_cases.action_case_id"]),
        sa.ForeignKeyConstraint(["task_id"], ["d9_action_case_tasks.task_id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"]),
        sa.UniqueConstraint("organization_id", "task_id", name="uq_d10_business_action_task"),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_d10_business_action_idem"),
    )
    op.create_index(
        "idx_d10_business_actions_case",
        "d10_business_actions",
        ["organization_id", "action_case_id", "created_at"],
    )

    op.create_table(
        "d10_outbox_events",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("business_action_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.String(), nullable=True),
        sa.Column("lease_owner", sa.String(), nullable=True),
        sa.Column("lease_until", sa.String(), nullable=True),
        sa.Column("published_at", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["business_action_id"], ["d10_business_actions.business_action_id"]),
        sa.UniqueConstraint("organization_id", "business_action_id", name="uq_d10_outbox_action"),
        sa.UniqueConstraint("organization_id", "dedupe_key", name="uq_d10_outbox_dedupe"),
    )
    op.create_index(
        "idx_d10_outbox_pending",
        "d10_outbox_events",
        ["organization_id", "status", "next_attempt_at", "created_at"],
    )

    op.create_table(
        "d10_idempotency_records",
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("business_action_id", sa.String(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "idempotency_key"),
    )

    op.create_table(
        "d10_audit_events",
        sa.Column("audit_id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=False),
        sa.Column("after_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["d10_business_actions.business_action_id"]),
    )
    op.create_index(
        "idx_d10_audit_entity",
        "d10_audit_events",
        ["organization_id", "entity_type", "entity_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_d10_audit_entity", table_name="d10_audit_events")
    op.drop_table("d10_audit_events")
    op.drop_table("d10_idempotency_records")
    op.drop_index("idx_d10_outbox_pending", table_name="d10_outbox_events")
    op.drop_table("d10_outbox_events")
    op.drop_index("idx_d10_business_actions_case", table_name="d10_business_actions")
    op.drop_table("d10_business_actions")
