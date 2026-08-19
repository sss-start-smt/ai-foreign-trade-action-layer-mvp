"""add tracking columns to order_import_batches

Revision ID: a1b2c3d4e5f6
Revises: e5a1b7c2d8f4
Create Date: 2026-08-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'e5a1b7c2d8f4'
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


def upgrade() -> None:
    _add_column_if_not('order_import_batches', sa.Column('started_at', sa.Text(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('preflight_completed_at', sa.Text(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('commit_completed_at', sa.Text(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('preflight_duration_ms', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('commit_duration_ms', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('end_to_end_duration_ms', sa.Integer(), nullable=True))
    _add_column_if_not('order_import_batches', sa.Column('processing_duration_ms', sa.Integer(), nullable=True))


def downgrade() -> None:
    for col in ['processing_duration_ms', 'end_to_end_duration_ms', 'commit_duration_ms',
                'preflight_duration_ms', 'commit_completed_at', 'preflight_completed_at', 'started_at']:
        try:
            op.drop_column('order_import_batches', col)
        except Exception:
            pass