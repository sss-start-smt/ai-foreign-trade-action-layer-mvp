"""D5 - conflict resolution, correction audit, and retry support

Revision ID: c8d9e1f2a3b4
Revises: b7c9e1d3f5a8
Create Date: 2026-08-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8d9e1f2a3b4'
down_revision: Union[str, None] = 'b7c9e1d3f5a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    return column_name in columns


def _add_column_if_not(table_name: str, column: sa.Column) -> None:
    if not _column_exists(table_name, column.name):
        try:
            op.add_column(table_name, column)
        except Exception:
            pass


def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _table_exists('order_corrections'):
        op.create_table(
            'order_corrections',
            sa.Column('correction_id', sa.Text(), primary_key=True),
            sa.Column('order_id', sa.Text(), nullable=False),
            sa.Column('source_order_key', sa.Text(), nullable=True),
            sa.Column('batch_id', sa.Text(), nullable=True),
            sa.Column('actor_user_id', sa.Text(), nullable=False),
            sa.Column('target_type', sa.Text(), nullable=False),
            sa.Column('target_id', sa.Text(), nullable=True),
            sa.Column('changes_json', sa.Text(), nullable=False),
            sa.Column('created_at', sa.Text(), nullable=False),
        )
        op.create_index(
            'idx_order_corrections_order',
            'order_corrections',
            ['order_id'],
        )
        op.create_index(
            'idx_order_corrections_batch',
            'order_corrections',
            ['batch_id'],
        )

    _add_column_if_not('order_import_batches', sa.Column('retry_of_batch_id', sa.Text(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('retry_attempt', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('duplicate_noop_count', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('conflict_count', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('corrected_count', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('source_file_name', sa.Text(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('source_file_size', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('file_sha256', sa.Text(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('has_header', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('start_row', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('organization_id', sa.Text(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('created_by', sa.Text(), nullable=True))

    _add_column_if_not('order_import_rows', sa.Column('source_system', sa.Text(), nullable=True))
    _add_column_if_not('order_import_rows', sa.Column('source_order_key', sa.Text(), nullable=True))
    _add_column_if_not('order_import_rows', sa.Column('source_line_key', sa.Text(), nullable=True))
    _add_column_if_not('order_import_rows', sa.Column('conflict_type', sa.Text(), nullable=True))
    _add_column_if_not('order_import_rows', sa.Column('conflict_details_json', sa.Text(), nullable=True))
    _add_column_if_not('order_import_rows', sa.Column('order_action', sa.Text(), nullable=True))


def downgrade() -> None:
    if _table_exists('order_corrections'):
        op.drop_table('order_corrections')

    for col in ['order_action', 'conflict_details_json', 'conflict_type',
                'source_line_key', 'source_order_key', 'source_system']:
        try:
            op.drop_column('order_import_rows', col)
        except Exception:
            pass

    for col in ['created_by', 'organization_id', 'start_row', 'has_header',
                'file_sha256', 'source_file_size', 'source_file_name',
                'corrected_count', 'conflict_count', 'duplicate_noop_count',
                'retry_attempt', 'retry_of_batch_id']:
        try:
            op.drop_column('order_import_batches', col)
        except Exception:
            pass