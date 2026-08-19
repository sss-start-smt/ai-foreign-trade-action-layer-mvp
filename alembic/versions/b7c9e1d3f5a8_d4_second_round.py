"""D4 second round - add projection_hash, warning/block counts, order_lines

Revision ID: b7c9e1d3f5a8
Revises: a1b2c3d4e5f6
Create Date: 2026-08-10 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c9e1d3f5a8'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
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
    _add_column_if_not('order_import_batches', sa.Column('projection_hash', sa.Text(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('warning_count', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('block_count', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('success_count', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('success_with_warning_count', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('commit_failed_count', sa.Integer(), nullable=True))

    _add_column_if_not('orders', sa.Column('source_system', sa.Text(), nullable=True))
    _add_column_if_not('orders', sa.Column('source_order_key', sa.Text(), nullable=True))

    if not _table_exists('order_lines'):
        op.create_table(
            'order_lines',
            sa.Column('line_id', sa.Text(), primary_key=True),
            sa.Column('order_id', sa.Text(), nullable=False),
            sa.Column('source_system', sa.Text(), nullable=True),
            sa.Column('source_order_key', sa.Text(), nullable=True),
            sa.Column('source_line_key', sa.Text(), nullable=True),
            sa.Column('product_name', sa.Text(), nullable=True),
            sa.Column('order_qty', sa.Integer(), nullable=True),
            sa.Column('completed_qty', sa.Integer(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.Text(), nullable=True),
            sa.Column('updated_at', sa.Text(), nullable=True),
        )


def downgrade() -> None:
    for col in ['commit_failed_count', 'success_with_warning_count', 'success_count',
                'block_count', 'warning_count', 'projection_hash']:
        try:
            op.drop_column('order_import_batches', col)
        except Exception:
            pass

    for col in ['source_order_key', 'source_system']:
        try:
            op.drop_column('orders', col)
        except Exception:
            pass

    if _table_exists('order_lines'):
        op.drop_table('order_lines')
