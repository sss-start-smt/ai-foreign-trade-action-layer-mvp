"""add D9 Task / Waiting / Trace tables

Revision ID: g9d0e1f2a3b4
Revises: f8a3b7c2d1e4
Create Date: 2026-08-13 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'g9d0e1f2a3b4'
down_revision: Union[str, None] = 'f8a3b7c2d1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'd9_action_case_tasks',
        sa.Column('task_id', sa.String(), primary_key=True),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('action_case_id', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('recommended_action', sa.Text(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default=sa.text("'TODO'")),
        sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['action_case_id'], ['action_cases.action_case_id']),
    )
    op.create_index('idx_d9_tasks_case_status', 'd9_action_case_tasks', ['action_case_id', 'status'])
    op.create_index('idx_d9_tasks_org_case', 'd9_action_case_tasks', ['organization_id', 'action_case_id'])

    op.create_table(
        'd9_action_case_waitings',
        sa.Column('waiting_id', sa.String(), primary_key=True),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), nullable=False),
        sa.Column('action_case_id', sa.String(), nullable=False),
        sa.Column('waiting_type', sa.String(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('due_at', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column('source_trace_id', sa.String(), nullable=True),
        sa.Column('reply_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('latest_reply_json', sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.Column('resolved_at', sa.String(), nullable=True),
        sa.Column('expired_at', sa.String(), nullable=True),
        sa.Column('cancelled_at', sa.String(), nullable=True),
        sa.Column('cancel_reason', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['d9_action_case_tasks.task_id']),
        sa.ForeignKeyConstraint(['action_case_id'], ['action_cases.action_case_id']),
    )
    op.create_index('idx_d9_waitings_case', 'd9_action_case_waitings', ['action_case_id'])
    op.create_index('idx_d9_waitings_task_status', 'd9_action_case_waitings', ['task_id', 'status'])
    op.create_index('idx_d9_waitings_due_scan', 'd9_action_case_waitings', ['organization_id', 'status', 'due_at'])
    op.create_index(
        'uq_d9_waitings_active',
        'd9_action_case_waitings',
        ['task_id'],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        'd9_trace_events',
        sa.Column('trace_id', sa.String(), primary_key=True),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('trace_kind', sa.String(), nullable=False),
        sa.Column('entity_type', sa.String(), nullable=False),
        sa.Column('entity_id', sa.String(), nullable=False),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('actor', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
    )
    op.create_index('idx_d9_trace_entity', 'd9_trace_events', ['entity_type', 'entity_id', 'created_at'])
    op.create_index('idx_d9_trace_org', 'd9_trace_events', ['organization_id'])


def downgrade() -> None:
    for idx in ('idx_d9_trace_org', 'idx_d9_trace_entity'):
        try:
            op.drop_index(idx, table_name='d9_trace_events')
        except Exception:
            pass
    try:
        op.drop_table('d9_trace_events')
    except Exception:
        pass
    try:
        op.drop_index('uq_d9_waitings_active', table_name='d9_action_case_waitings')
    except Exception:
        pass
    for idx in ('idx_d9_waitings_due_scan', 'idx_d9_waitings_task_status', 'idx_d9_waitings_case'):
        try:
            op.drop_index(idx, table_name='d9_action_case_waitings')
        except Exception:
            pass
    try:
        op.drop_table('d9_action_case_waitings')
    except Exception:
        pass
    for idx in ('idx_d9_tasks_org_case', 'idx_d9_tasks_case_status'):
        try:
            op.drop_index(idx, table_name='d9_action_case_tasks')
        except Exception:
            pass
    try:
        op.drop_table('d9_action_case_tasks')
    except Exception:
        pass
