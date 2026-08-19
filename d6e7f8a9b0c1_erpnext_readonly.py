"""D6 - ERPNext Read-Only Integration

Revision ID: d6e7f8a9b0c1
Revises: c8d9e1f2a3b4
Create Date: 2026-08-11 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, None] = 'c8d9e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _table_exists('erp_sync_state'):
        op.create_table(
            'erp_sync_state',
            sa.Column('organization_id', sa.Text(), nullable=False),
            sa.Column('doctype', sa.Text(), nullable=False),
            sa.Column('last_success_cursor', sa.Text(), nullable=True),
            sa.Column('last_success_at', sa.Text(), nullable=True),
            sa.Column('last_attempt_at', sa.Text(), nullable=True),
            sa.Column('sync_status', sa.Text(), nullable=False, server_default=sa.text("'NEVER_SYNCED'")),
            sa.Column('last_error_code', sa.Text(), nullable=True),
            sa.Column('records_seen', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('records_changed', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('updated_at', sa.Text(), nullable=False),
            sa.PrimaryKeyConstraint('organization_id', 'doctype'),
        )
        op.create_index(
            'idx_erp_sync_state_org',
            'erp_sync_state',
            ['organization_id'],
        )
        op.create_index(
            'idx_erp_sync_state_status',
            'erp_sync_state',
            ['sync_status'],
        )

    if not _table_exists('erp_read_snapshots'):
        op.create_table(
            'erp_read_snapshots',
            sa.Column('snapshot_id', sa.Text(), primary_key=True),
            sa.Column('organization_id', sa.Text(), nullable=False),
            sa.Column('doctype', sa.Text(), nullable=False),
            sa.Column('external_id', sa.Text(), nullable=False),
            sa.Column('source_modified_at', sa.Text(), nullable=True),
            sa.Column('normalized_json', sa.Text(), nullable=False),
            sa.Column('raw_sha256', sa.Text(), nullable=False),
            sa.Column('fetched_at', sa.Text(), nullable=False),
            sa.Column('created_at', sa.Text(), nullable=False),
            sa.Column('updated_at', sa.Text(), nullable=False),
            sa.UniqueConstraint('organization_id', 'doctype', 'external_id'),
        )
        op.create_index(
            'idx_erp_snapshots_org',
            'erp_read_snapshots',
            ['organization_id', 'doctype'],
        )
        op.create_index(
            'idx_erp_snapshots_external',
            'erp_read_snapshots',
            ['doctype', 'external_id'],
        )
        op.create_index(
            'idx_erp_snapshots_fetched',
            'erp_read_snapshots',
            ['fetched_at'],
        )


def downgrade() -> None:
    if _table_exists('erp_read_snapshots'):
        op.drop_table('erp_read_snapshots')
    if _table_exists('erp_sync_state'):
        op.drop_table('erp_sync_state')