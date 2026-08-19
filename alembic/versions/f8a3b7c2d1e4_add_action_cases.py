"""add action_cases table for D8

Revision ID: f8a3b7c2d1e4
Revises: d6e7f8a9b0c1
Create Date: 2026-08-12 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f8a3b7c2d1e4'
down_revision: Union[str, None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'action_cases',
        sa.Column('action_case_id', sa.String(), primary_key=True),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('order_id', sa.String(), nullable=False),
        sa.Column('action_intent_key', sa.String(), nullable=False),
        sa.Column('intent_type', sa.String(), nullable=False),
        sa.Column('stage', sa.String(), nullable=False),
        sa.Column('lifecycle_status', sa.String(), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('latest_action_bucket', sa.String(), nullable=True),
        sa.Column('latest_severity', sa.String(), nullable=True),
        sa.Column('latest_recommended_action', sa.String(), nullable=True),
        sa.Column('latest_evidence_json', sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('observation_status', sa.String(), nullable=False, server_default=sa.text("'OBSERVED'")),
        sa.Column('first_seen_at', sa.String(), nullable=False),
        sa.Column('last_seen_at', sa.String(), nullable=False),
        sa.Column('last_reconciled_at', sa.String(), nullable=True),
        sa.Column('source_policy_version', sa.String(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('close_reason', sa.String(), nullable=True),
        sa.Column('closed_at', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.order_id']),
    )

    op.create_index(
        'uq_action_cases_active',
        'action_cases',
        ['organization_id', 'order_id', 'action_intent_key'],
        unique=True,
        postgresql_where=sa.text("lifecycle_status = 'ACTIVE'"),
        sqlite_where=sa.text("lifecycle_status = 'ACTIVE'"),
    )

    op.create_index(
        'idx_action_cases_org_order',
        'action_cases',
        ['organization_id', 'order_id', 'lifecycle_status'],
    )

    op.create_index(
        'idx_action_cases_stage',
        'action_cases',
        ['stage', 'lifecycle_status'],
    )

    op.create_index(
        'idx_action_cases_intent',
        'action_cases',
        ['action_intent_key', 'lifecycle_status'],
    )


def downgrade() -> None:
    try:
        op.drop_index('idx_action_cases_intent', table_name='action_cases')
    except Exception:
        pass
    try:
        op.drop_index('idx_action_cases_stage', table_name='action_cases')
    except Exception:
        pass
    try:
        op.drop_index('idx_action_cases_org_order', table_name='action_cases')
    except Exception:
        pass
    try:
        op.drop_index('uq_action_cases_active', table_name='action_cases')
    except Exception:
        pass
    try:
        op.drop_table('action_cases')
    except Exception:
        pass