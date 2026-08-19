"""add D12 human review / approval gate

Revision ID: l3m4n5o6p7q8
Revises: k2l3m4n5o6p7
Create Date: 2026-08-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "l3m4n5o6p7q8"
down_revision: Union[str, None] = "k2l3m4n5o6p7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "d12_human_reviews",
        sa.Column("review_id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("order_id", sa.String(), nullable=False),
        sa.Column("action_case_id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("state_version", sa.String(), nullable=False),
        sa.Column("state_snapshot_json", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("requester_role", sa.String(), nullable=False),
        sa.Column("required_review", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("d10_request_id", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(), nullable=True),
        sa.Column("reviewer_role", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("reviewed_at", sa.String(), nullable=True),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("consumed_at", sa.String(), nullable=True),
        sa.Column("business_action_id", sa.String(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"]),
        sa.ForeignKeyConstraint(["action_case_id"], ["action_cases.action_case_id"]),
        sa.ForeignKeyConstraint(["task_id"], ["d9_action_case_tasks.task_id"]),
        sa.ForeignKeyConstraint(["business_action_id"], ["d10_business_actions.business_action_id"]),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_d12_review_org_idem"),
    )
    op.create_index(
        "idx_d12_reviews_queue",
        "d12_human_reviews",
        ["organization_id", "status", "required_review", "created_at"],
    )
    op.create_index(
        "idx_d12_reviews_task",
        "d12_human_reviews",
        ["organization_id", "task_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_d12_reviews_task", table_name="d12_human_reviews")
    op.drop_index("idx_d12_reviews_queue", table_name="d12_human_reviews")
    op.drop_table("d12_human_reviews")
