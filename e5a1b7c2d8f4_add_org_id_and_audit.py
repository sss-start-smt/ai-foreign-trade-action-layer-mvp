"""add organization_id columns and audit_logs table for RBAC

Revision ID: e5a1b7c2d8f4
Revises: d45b6b640e68
Create Date: 2026-08-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5a1b7c2d8f4'
down_revision: Union[str, None] = 'd45b6b640e68'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    return column_name in columns


def _index_exists(table_name: str, index_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
    return index_name in indexes


def _add_column_if_not(table_name: str, column: sa.Column) -> None:
    if not _column_exists(table_name, column.name):
        try:
            op.add_column(table_name, column)
        except Exception:
            pass


def _create_index_if_not(table_name: str, index_name: str, columns: list) -> None:
    if not _index_exists(table_name, index_name):
        try:
            op.create_index(index_name, table_name, columns)
        except Exception:
            pass


def upgrade() -> None:
    _add_column_if_not('orders', sa.Column('organization_id', sa.String(), nullable=True))
    _create_index_if_not('orders', 'idx_orders_org', ['organization_id'])

    _add_column_if_not('tasks', sa.Column('organization_id', sa.String(), nullable=True))
    _create_index_if_not('tasks', 'idx_tasks_org', ['organization_id'])

    _add_column_if_not('event_logs', sa.Column('organization_id', sa.String(), nullable=True))
    _create_index_if_not('event_logs', 'idx_events_org', ['organization_id'])

    _add_column_if_not('agent_runs', sa.Column('organization_id', sa.String(), nullable=True))
    _create_index_if_not('agent_runs', 'idx_runs_org', ['organization_id'])

    _add_column_if_not('approval_requests', sa.Column('organization_id', sa.String(), nullable=True))
    _create_index_if_not('approval_requests', 'idx_approvals_org', ['organization_id'])

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if 'audit_logs' not in existing_tables:
        op.create_table(
            'audit_logs',
            sa.Column('audit_id', sa.String(), primary_key=True),
            sa.Column('organization_id', sa.String(), nullable=False),
            sa.Column('actor_user_id', sa.String(), nullable=False),
            sa.Column('actor_role', sa.String(), nullable=False),
            sa.Column('action', sa.String(), nullable=False),
            sa.Column('entity_type', sa.String(), nullable=False),
            sa.Column('entity_id', sa.String(), nullable=False),
            sa.Column('result', sa.String(), nullable=False, server_default=sa.text("'SUCCESS'")),
            sa.Column('details_json', sa.Text(), nullable=False),
            sa.Column('created_at', sa.String(), nullable=False),
        )
        _create_index_if_not('audit_logs', 'idx_audit_org', ['organization_id'])
        _create_index_if_not('audit_logs', 'idx_audit_actor', ['actor_user_id'])
        _create_index_if_not('audit_logs', 'idx_audit_action', ['action'])


def downgrade() -> None:
    try:
        op.drop_index('idx_audit_action', table_name='audit_logs')
    except Exception:
        pass
    try:
        op.drop_index('idx_audit_actor', table_name='audit_logs')
    except Exception:
        pass
    try:
        op.drop_index('idx_audit_org', table_name='audit_logs')
    except Exception:
        pass
    try:
        op.drop_table('audit_logs')
    except Exception:
        pass

    try:
        op.drop_index('idx_approvals_org', table_name='approval_requests')
    except Exception:
        pass
    try:
        op.drop_column('approval_requests', 'organization_id')
    except Exception:
        pass

    try:
        op.drop_index('idx_runs_org', table_name='agent_runs')
    except Exception:
        pass
    try:
        op.drop_column('agent_runs', 'organization_id')
    except Exception:
        pass

    try:
        op.drop_index('idx_events_org', table_name='event_logs')
    except Exception:
        pass
    try:
        op.drop_column('event_logs', 'organization_id')
    except Exception:
        pass

    try:
        op.drop_index('idx_tasks_org', table_name='tasks')
    except Exception:
        pass
    try:
        op.drop_column('tasks', 'organization_id')
    except Exception:
        pass

    try:
        op.drop_index('idx_orders_org', table_name='orders')
    except Exception:
        pass
    try:
        op.drop_column('orders', 'organization_id')
    except Exception:
        pass
